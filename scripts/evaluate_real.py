"""真实模型基线运行器：固定题集、真实调用、可复核报告。

不调 prompt、不改语义层、不自动刷新任何预期。一次运行写四份产物：
``report.json``、``report.md``、``calls.jsonl``、``review.md``，保存在
``eval/real/<run_id>/``，该目录不提交。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

import yaml

from app.config import Settings
from app.engine.clarify.session import ClarificationService, MemorySessionStore
from app.engine.compiler import CompileError, compile_sql
from app.engine.execution import ExecutionError, SQLiteExecutor
from app.engine.query import QueryAnswer, QueryService, QueryValidationError
from app.engine.scope import Scope
from app.engine.semantic.bank_mini import semantic_catalog, validation_catalog
from app.engine.understanding import ExtractionError, extract_intent
from app.engine.understanding.context import PROTOCOL_VERSION
from app.llm.factory import build_llm
from app.llm.protocol import LLMClient, LLMTransportError
from scripts.call_recorder import BudgetExhausted, CallRecord, RecordingLLM, RunAborted
from scripts.golden import compare, load_cases
from scripts.judging import Verdict, judge_execution, judge_extraction
from scripts.seed_sqlite_business import seed_business

ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_CASES = ROOT / "tests/fixtures/intent_extraction/cases.yaml"
EXECUTION_CASES = ROOT / "eval/cases/asset_quality.yaml"
GOLDEN_DIR = ROOT / "eval/golden"
REAL_DIR = ROOT / "eval/real"

CATALOG_REVISION = "bank-mini-v1"
TEMPERATURE = 0.0
MAX_TOKENS = 2048
TIMEOUT_S = 30.0

CONCLUSION_BOUNDARY = (
    "题集只有 30 题且都是规范问法；只测了一个模型的一个版本；temperature 为 0 也不保证逐字可复现；"
    "结论不代表真实用户问法的表现，也不代表可以上生产。"
)

ClientFactory = Callable[[Settings], LLMClient]


# ---------------------------------------------------------------------------
# 数据加载与判定记录
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_extraction_cases() -> list[dict[str, Any]]:
    payload = _load_yaml(EXTRACTION_CASES)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"{EXTRACTION_CASES} 结构不符合预期。")
    return list(payload["cases"])


def _load_execution_cases() -> list[dict[str, Any]]:
    payload = _load_yaml(EXECUTION_CASES)
    if not isinstance(payload, list):
        raise ValueError(f"{EXECUTION_CASES} 结构不符合预期。")
    return list(payload)


def _select(cases: Sequence[dict[str, Any]], case_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    if not case_ids:
        return list(cases)
    selected = set(case_ids)
    return [case for case in cases if str(case["id"]) in selected]


def _filtered_cases(case_ids: Sequence[str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extraction = _load_extraction_cases()
    execution = _load_execution_cases()
    if case_ids:
        known = {str(case["id"]) for case in (*extraction, *execution)}
        unknown = sorted(set(case_ids) - known)
        if unknown:
            raise ValueError(f"不认识的用例：{', '.join(unknown)}")
    return _select(extraction, case_ids), _select(execution, case_ids)


def _attempt(
    suite: str,
    case_id: str,
    attempt_no: int,
    verdict: Verdict,
    *,
    elapsed_ms: float,
    call_indexes: Sequence[int],
    transport: bool = False,
    golden_matches: bool | None = None,
) -> dict[str, Any]:
    return {
        "suite": suite,
        "case_id": case_id,
        "attempt": attempt_no,
        "ok": verdict.ok,
        "stage": "" if verdict.ok else verdict.stage,
        "code": "" if verdict.ok else verdict.code,
        "message": "" if verdict.ok else verdict.message,
        "transport": transport,
        "golden_matches": golden_matches,
        "elapsed_ms": elapsed_ms,
        "call_indexes": list(call_indexes),
    }


def _error_verdict(error: Exception) -> Verdict:
    if isinstance(error, CompileError):
        return Verdict(False, "compiler", error.code, str(error))
    if isinstance(error, QueryValidationError):
        return Verdict(False, "validation", "QUERY_VALIDATION_FAILED", str(error))
    if isinstance(error, ExecutionError):
        return Verdict(False, "execution", error.code, str(error))
    return Verdict(False, "runner", type(error).__name__, str(error))


def _transport_attempt(
    suite: str,
    case_id: str,
    attempt_no: int,
    error: Exception,
    *,
    elapsed_ms: float,
    call_indexes: Sequence[int],
) -> dict[str, Any]:
    return _attempt(
        suite,
        case_id,
        attempt_no,
        Verdict(False, "transport", type(error).__name__, str(error)),
        elapsed_ms=elapsed_ms,
        call_indexes=call_indexes,
        transport=True,
    )


def _exclusion_reason(case: Mapping[str, Any]) -> str | None:
    measurement = case.get("measurement")
    if measurement is None:
        return None
    if not isinstance(measurement, Mapping):
        raise ValueError(f"用例 {case.get('id')} 的 measurement 必须是映射。")
    status = str(measurement.get("status") or "").strip()
    if not status:
        return None
    if status != "excluded":
        raise ValueError(f"用例 {case.get('id')} 的 measurement.status 只能是 excluded，当前为 {status!r}。")
    reason = str(measurement.get("reason") or "").strip()
    if not reason:
        raise ValueError(f"用例 {case.get('id')} 的 measurement.reason 不能为空。")
    return reason


def summarize_suite(
    name: str,
    attempts: Sequence[dict[str, Any]],
    repeats: int,
    excluded_cases: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """按题汇总：全对率、多数通过率、不稳定题；transport 不进准确率分母。"""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        by_case.setdefault(str(attempt["case_id"]), []).append(attempt)

    completed_cases: list[str] = []
    incomplete_cases: list[str] = []
    full_pass_cases: list[str] = []
    majority_pass_cases: list[str] = []
    unstable_cases: list[str] = []

    for case_id, case_attempts in sorted(by_case.items()):
        transport = [item for item in case_attempts if item.get("transport")]
        if transport or len(case_attempts) < repeats:
            incomplete_cases.append(case_id)
            continue
        completed_cases.append(case_id)
        ok_count = sum(1 for item in case_attempts if item.get("ok"))
        if ok_count == repeats:
            full_pass_cases.append(case_id)
        if ok_count >= repeats // 2 + 1:
            majority_pass_cases.append(case_id)
        if 0 < ok_count < repeats:
            unstable_cases.append(case_id)

    counted = [attempt for attempt in attempts if not attempt.get("transport")]
    ok_attempts = sum(1 for attempt in counted if attempt.get("ok"))
    total_cases = len(by_case) + len(excluded_cases)
    return {
        "name": name,
        "total_cases": total_cases,
        "completed_cases": len(completed_cases),
        "incomplete_cases": incomplete_cases,
        "excluded_cases": [dict(item) for item in excluded_cases],
        "full_pass_cases": len(full_pass_cases),
        "full_pass_rate": _rate(len(full_pass_cases), len(completed_cases)),
        "majority_pass_cases": len(majority_pass_cases),
        "majority_pass_rate": _rate(len(majority_pass_cases), len(completed_cases)),
        "unstable_cases": unstable_cases,
        "attempts": len(attempts),
        "counted_attempts": len(counted),
        "ok_attempts": ok_attempts,
        "attempt_accuracy": _rate(ok_attempts, len(counted)),
        "transport_attempts": len(attempts) - len(counted),
        "model_calls": sum(len(attempt.get("call_indexes", ())) for attempt in attempts),
        "elapsed_ms": round(sum(float(attempt.get("elapsed_ms", 0.0)) for attempt in attempts), 3),
    }


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 6) if whole else 0.0


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _fixture_scope() -> Scope:
    tables = sorted({table for _, table in validation_catalog().all_tables()})
    return Scope.of(tables)


def _golden_map() -> dict[str, Any]:
    return {
        case.eval_case: case
        for case in load_cases(GOLDEN_DIR)
        if case.eval_case and case.sql_path is not None
    }


def _golden_matches(
    case_id: str, answer: QueryAnswer, *, scope: Scope, golden_cases: dict[str, Any]
) -> bool | None:
    golden = golden_cases.get(case_id)
    if golden is None or golden.sql_path is None or answer.session.intent is None:
        return None
    try:
        produced = compile_sql(answer.session.intent, semantic_catalog(), scope=scope, dialect="sqlite")
        return compare(golden.sql_path.read_text(encoding="utf-8"), produced).matches
    except Exception:
        return None


async def _run_extraction(
    recorder: RecordingLLM,
    cases: Sequence[dict[str, Any]],
    repeats: int,
    attempts: list[dict[str, Any]],
) -> None:
    for case in cases:
        for attempt_no in range(1, repeats + 1):
            started = time.perf_counter()
            call_start = recorder.call_count
            try:
                result = await extract_intent(case["question"], semantic_catalog(), llm=recorder)
            except ExtractionError as exc:
                verdict = judge_extraction(case, error=exc)
            except LLMTransportError as exc:
                attempts.append(
                    _transport_attempt(
                        "extraction",
                        str(case["id"]),
                        attempt_no,
                        exc,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                        call_indexes=range(call_start + 1, recorder.call_count + 1),
                    )
                )
                continue
            except (BudgetExhausted, RunAborted):
                raise
            except Exception as exc:
                verdict = _error_verdict(exc)
            else:
                try:
                    verdict = judge_extraction(case, intent=result.intent)
                except Exception as exc:
                    verdict = _error_verdict(exc)
            attempts.append(
                _attempt(
                    "extraction",
                    str(case["id"]),
                    attempt_no,
                    verdict,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                    call_indexes=range(call_start + 1, recorder.call_count + 1),
                )
            )


async def _run_execution(
    recorder: RecordingLLM,
    cases: Sequence[dict[str, Any]],
    repeats: int,
    attempts: list[dict[str, Any]],
    *,
    database: Path,
    scope: Scope,
    golden_cases: dict[str, Any],
    excluded_cases: list[dict[str, Any]],
) -> None:
    measured: list[dict[str, Any]] = []
    for case in cases:
        reason = _exclusion_reason(case)
        if reason is None:
            measured.append(case)
        else:
            excluded_cases.append({"suite": "execution", "case_id": str(case["id"]), "reason": reason})
    for case in measured:
        for attempt_no in range(1, repeats + 1):
            started = time.perf_counter()
            call_start = recorder.call_count
            service = QueryService(
                ClarificationService(store=MemorySessionStore(), llm=recorder),
                semantic_catalog(),
                validation_catalog(),
                SQLiteExecutor(database),
            )
            try:
                result = await service.start(
                    case["question"],
                    owner_key=f"evaluate-real-{case['id']}-{attempt_no}",
                    catalog_revision=CATALOG_REVISION,
                    scope=scope,
                )
            except LLMTransportError as exc:
                attempts.append(
                    _transport_attempt(
                        "execution",
                        str(case["id"]),
                        attempt_no,
                        exc,
                        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                        call_indexes=range(call_start + 1, recorder.call_count + 1),
                    )
                )
                continue
            except (BudgetExhausted, RunAborted):
                raise
            except Exception as exc:
                verdict = (
                    judge_execution(case, error=exc)
                    if isinstance(exc, (ExtractionError, CompileError, QueryValidationError, ExecutionError))
                    else _error_verdict(exc)
                )
                golden_matches = None
            else:
                try:
                    verdict = judge_execution(case, result)
                except Exception as exc:
                    verdict = _error_verdict(exc)
                    golden_matches = None
                else:
                    golden_matches = (
                        _golden_matches(str(case["id"]), result, scope=scope, golden_cases=golden_cases)
                        if isinstance(result, QueryAnswer)
                        else None
                    )
            attempts.append(
                _attempt(
                    "execution",
                    str(case["id"]),
                    attempt_no,
                    verdict,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                    call_indexes=range(call_start + 1, recorder.call_count + 1),
                    golden_matches=golden_matches,
                )
            )


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _metadata(
    settings: Settings,
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    calls: Sequence[CallRecord],
    case_ids: Sequence[str] | None,
    repeats: int,
    max_calls: int,
    aborted: bool,
    budget_exhausted: bool,
    abort_reason: str,
    status: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "git_commit": _git_commit(),
        "model_requested": settings.llm_model,
        "model_versions": sorted({record.model for record in calls if record.model}),
        "api_host": urlsplit(settings.llm_base_url).netloc,
        "protocol_version": PROTOCOL_VERSION,
        "catalog_revision": CATALOG_REVISION,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "timeout_s": TIMEOUT_S,
        "thinking": settings.llm_thinking,
        "repeats": repeats,
        "max_calls": max_calls,
        "case_filter": list(case_ids) if case_ids else None,
        "extraction_cases_sha256": _sha256(EXTRACTION_CASES),
        "execution_cases_sha256": _sha256(EXECUTION_CASES),
        "total_calls": len(calls),
        "total_tokens": sum(record.total_tokens for record in calls),
        "aborted": aborted,
        "budget_exhausted": budget_exhausted,
        "abort_reason": abort_reason,
        "status": status,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _build_report(
    settings: Settings,
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    recorder: RecordingLLM,
    extraction_attempts: Sequence[dict[str, Any]],
    execution_attempts: Sequence[dict[str, Any]],
    excluded_cases: Sequence[Mapping[str, Any]],
    case_ids: Sequence[str] | None,
    repeats: int,
    max_calls: int,
    aborted: bool,
    budget_exhausted: bool,
    abort_reason: str,
) -> dict[str, Any]:
    suites = [
        summarize_suite("extraction", extraction_attempts, repeats),
        summarize_suite("execution", execution_attempts, repeats, excluded_cases),
    ]
    all_attempts = [*extraction_attempts, *execution_attempts]
    failed = any(not attempt["ok"] for attempt in all_attempts)
    incomplete = any(suite["incomplete_cases"] for suite in suites)
    if aborted:
        status = "budget_exhausted" if budget_exhausted else "aborted"
    elif failed or incomplete:
        status = "failed"
    else:
        status = "ok"
    for suite in suites:
        suite["attempts_detail"] = [attempt for attempt in all_attempts if attempt["suite"] == suite["name"]]
    return {
        "version": 1,
        "run_id": run_id,
        "metadata": _metadata(
            settings,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            calls=recorder.calls,
            case_ids=case_ids,
            repeats=repeats,
            max_calls=max_calls,
            aborted=aborted,
            budget_exhausted=budget_exhausted,
            abort_reason=abort_reason,
            status=status,
        ),
        "suites": suites,
        "status": status,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    lines = [
        "# 真实模型基线报告",
        "",
        f"- run_id：`{metadata['run_id']}`",
        f"- 模型：`{metadata['model_requested']}`（实际返回：`{', '.join(metadata['model_versions']) or '未记录'}`）",
        f"- git commit：`{metadata['git_commit']}`",
        f"- thinking：`{metadata['thinking']}`；temperature：{metadata['temperature']}；重复：{metadata['repeats']}",
        f"- 调用预算：{metadata['max_calls']}；总调用：{metadata['total_calls']}；总 token：{metadata['total_tokens']}",
        f"- 状态：`{metadata['status']}`",
        "",
        "## 套件汇总",
        "",
        "| 套件 | 总题 | 完成题 | 排除题 | 全对率 | 多数通过率 | 不稳定题 | 未完成题 | 尝试准确率 | 模型调用 | 耗时(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for suite in report["suites"]:
        lines.append(
            f"| {suite['name']} | {suite['total_cases']} | {suite['completed_cases']} | "
            f"{len(suite['excluded_cases'])} | {suite['full_pass_rate']:.2%} | "
            f"{suite['majority_pass_rate']:.2%} | {len(suite['unstable_cases'])} | "
            f"{len(suite['incomplete_cases'])} | {suite['attempt_accuracy']:.2%} | "
            f"{suite['model_calls']} | {suite['elapsed_ms']:.3f} |"
        )

    failures = [
        attempt for suite in report["suites"] for attempt in suite["attempts_detail"] if not attempt["ok"]
    ]
    lines += ["", "## 失败归因", ""]
    if not failures:
        lines.append("无失败样本。")
    else:
        lines += ["| 样本 | 次数 | 阶段 | 错误码 | 说明 |", "|---|---:|---|---|---|"]
        lines.extend(
            f"| {attempt['case_id']} | {attempt['attempt']} | {attempt['stage']} | "
            f"{attempt['code']} | {attempt['message']} |"
            for attempt in failures
        )

    lines += ["", "## 不稳定题", ""]
    unstable = [
        f"{suite['name']}：{', '.join(suite['unstable_cases'])}"
        for suite in report["suites"]
        if suite["unstable_cases"]
    ]
    lines.append("\n".join(unstable) if unstable else "无。")

    lines += ["", "## 未完成题", ""]
    incomplete = [
        f"{suite['name']}：{', '.join(suite['incomplete_cases'])}"
        for suite in report["suites"]
        if suite["incomplete_cases"]
    ]
    lines.append("\n".join(incomplete) if incomplete else "无。")
    lines.append("transport 失败不计入准确率分母；受影响题在完成题中排除。")

    lines += ["", "## 排除题（不计入准确率）", ""]
    excluded = [
        f"{suite['name']}：{item['case_id']}（{item['reason']}）"
        for suite in report["suites"]
        for item in suite["excluded_cases"]
    ]
    lines.append("\n".join(excluded) if excluded else "无。")

    lines += ["", "## 结论边界", "", CONCLUSION_BOUNDARY, ""]
    return "\n".join(lines)


def _call_texts(calls: Sequence[CallRecord], indexes: Sequence[int]) -> str:
    texts = []
    for index in indexes:
        if 1 <= index <= len(calls):
            text = calls[index - 1].text.replace("\n", " ").strip()
            if text:
                texts.append(text[:200])
    return " | ".join(texts)


def _render_review(
    report: dict[str, Any],
    calls: Sequence[CallRecord],
    *,
    extraction_cases: dict[str, dict[str, Any]],
    execution_cases: dict[str, dict[str, Any]],
) -> str:
    lines = [
        "# 人工归因模板",
        "",
        "每条失败填写「归因」和「下一步动作」。不要修改题集或预期来让本轮变绿。",
        "",
    ]
    failures = [
        attempt for suite in report["suites"] for attempt in suite["attempts_detail"] if not attempt["ok"]
    ]
    if not failures:
        lines.append("无失败样本。")

    for attempt in failures:
        case = (
            extraction_cases.get(attempt["case_id"])
            if attempt["suite"] == "extraction"
            else execution_cases.get(attempt["case_id"])
        ) or {}
        expected = case.get("expected", {})
        lines += [
            f"## {attempt['case_id']} / 第 {attempt['attempt']} 次 / {attempt['stage']} / {attempt['code']}",
            "",
            f"- 说明：{attempt['message']}",
            f"- 预期：`{json.dumps(expected, ensure_ascii=False, default=str)}`",
            f"- 模型原文：{_call_texts(calls, attempt['call_indexes']) or '（无调用记录）'}",
            "- 归因：",
            "- 下一步动作：",
            "",
        ]

    excluded = [
        f"- {suite['name']} / {item['case_id']}：{item['reason']}"
        for suite in report["suites"]
        for item in suite["excluded_cases"]
    ]
    if excluded:
        lines += ["", "## 排除题（不计入准确率）", "", *excluded, ""]
    return "\n".join(lines)


def _write_outputs(
    output_dir: Path,
    report: dict[str, Any],
    calls: Sequence[CallRecord],
    *,
    extraction_cases: dict[str, dict[str, Any]],
    execution_cases: dict[str, dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(_render_markdown(report) + "\n", encoding="utf-8")
    with (output_dir / "calls.jsonl").open("w", encoding="utf-8") as stream:
        for record in calls:
            stream.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    (output_dir / "review.md").write_text(
        _render_review(
            report,
            calls,
            extraction_cases=extraction_cases,
            execution_cases=execution_cases,
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _new_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{_git_commit()[:7]}"


def _preflight(settings: Settings) -> bool:
    if settings.llm_mode != "real":
        _warn("llm_mode 必须是 real，当前不是。")
        return False
    if not settings.llm_api_key:
        _warn("WENSHU_LLM_API_KEY 为空。")
        return False
    if not settings.llm_base_url or not settings.llm_model:
        _warn("WENSHU_LLM_BASE_URL 或 WENSHU_LLM_MODEL 为空。")
        return False
    if settings.llm_thinking != "disabled":
        _warn("WENSHU_LLM_THINKING 必须是 disabled。")
        return False
    return True


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


async def run(
    settings: Settings,
    *,
    repeats: int = 3,
    case_ids: Sequence[str] | None = None,
    max_calls: int = 200,
    client_factory: ClientFactory = build_llm,
    output_root: Path = REAL_DIR,
    run_id: str | None = None,
) -> int:
    if type(repeats) is not int or repeats <= 0:
        _warn("--repeats 必须是正整数。")
        return 2
    if type(max_calls) is not int or max_calls <= 0:
        _warn("--max-calls 必须是正整数。")
        return 2
    if not _preflight(settings):
        return 2

    try:
        extraction_cases, execution_cases = _filtered_cases(case_ids)
    except ValueError as exc:
        _warn(str(exc))
        return 2

    run_id = run_id or _new_run_id()
    output_dir = Path(output_root) / run_id
    if output_dir.exists():
        _warn(f"输出目录已存在：{output_dir}")
        return 2

    extraction_map = {str(case["id"]): case for case in extraction_cases}
    execution_map = {str(case["id"]): case for case in execution_cases}
    recorder = RecordingLLM(client_factory(settings), max_calls=max_calls)
    output_dir.mkdir(parents=True, exist_ok=False)

    started_at = datetime.now(UTC)
    extraction_attempts: list[dict[str, Any]] = []
    execution_attempts: list[dict[str, Any]] = []
    excluded_cases: list[dict[str, Any]] = []
    aborted = False
    budget_exhausted = False
    abort_reason = ""

    try:
        with TemporaryDirectory(prefix="wenshu-real-eval-") as directory:
            database = Path(directory) / "business.db"
            seed_business(database)
            scope = _fixture_scope()
            golden_cases = _golden_map()
            await _run_extraction(recorder, extraction_cases, repeats, extraction_attempts)
            await _run_execution(
                recorder,
                execution_cases,
                repeats,
                execution_attempts,
                database=database,
                scope=scope,
                golden_cases=golden_cases,
                excluded_cases=excluded_cases,
            )
    except BudgetExhausted as exc:
        aborted = True
        budget_exhausted = True
        abort_reason = str(exc)
    except RunAborted as exc:
        aborted = True
        abort_reason = str(exc)
    except Exception as exc:
        aborted = True
        abort_reason = f"runner: {type(exc).__name__}: {exc}"
    finally:
        finished_at = datetime.now(UTC)
        report = _build_report(
            settings,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            recorder=recorder,
            extraction_attempts=extraction_attempts,
            execution_attempts=execution_attempts,
            excluded_cases=excluded_cases,
            case_ids=case_ids,
            repeats=repeats,
            max_calls=max_calls,
            aborted=aborted,
            budget_exhausted=budget_exhausted,
            abort_reason=abort_reason,
        )
        _write_outputs(
            output_dir,
            report,
            recorder.calls,
            extraction_cases=extraction_map,
            execution_cases=execution_map,
        )

    if aborted:
        return 3
    all_attempts = [*extraction_attempts, *execution_attempts]
    if not all_attempts:
        return 0 if excluded_cases else 1
    if any(not attempt["ok"] for attempt in all_attempts):
        return 1
    if any(suite["incomplete_cases"] for suite in report["suites"]):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case", action="append", dest="cases", metavar="CASE_ID")
    parser.add_argument("--max-calls", type=int, default=200)
    args = parser.parse_args(argv)
    if args.repeats <= 0 or args.max_calls <= 0:
        parser.error("--repeats 和 --max-calls 必须是正整数。")
    return asyncio.run(
        run(
            Settings.from_env(),
            repeats=args.repeats,
            case_ids=args.cases,
            max_calls=args.max_calls,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
