"""运行固定回放评测并生成 JSON/Markdown 报告。"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from app.engine.clarify import prepare_question
from app.engine.clarify.session import ClarificationService, MemorySessionStore
from app.engine.execution import SQLiteExecutor
from app.engine.query import QueryService
from app.engine.scope import Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.understanding import ExtractionError, extract_intent
from app.llm.replay import ReplayLLM
from scripts.golden import CaseOutcome, evaluate_case, load_cases, load_eval_case_ids
from scripts.judging import judge_execution, judge_extraction
from scripts.seed_sqlite_business import seed_business

ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_CASES = ROOT / "tests/fixtures/intent_extraction/cases.yaml"
CLARIFICATION_CASES = ROOT / "tests/fixtures/clarification/cases.yaml"
GOLDEN_DIR = ROOT / "eval/golden"
EVAL_DIR = ROOT / "eval/cases"
AQ_001_QUESTION = "2026年4月末全行贷款余额是多少？"
AQ_001_CASSETTE = ROOT / "tests/cassettes/query_execution/AQ-001.json"


def _record(case_id: str, *, elapsed_ms: float, model_calls: int) -> dict[str, Any]:
    return {
        "id": case_id,
        "ok": False,
        "failure": None,
        "elapsed_ms": round(elapsed_ms, 3),
        "model_calls": model_calls,
    }


def _fail(record: dict[str, Any], stage: str, code: str, message: str) -> None:
    record["failure"] = {"stage": stage, "code": code, "message": message}


def _suite(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(bool(record["ok"]) for record in records)
    return {
        "name": name,
        "total": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "accuracy": round(passed / len(records), 6) if records else 0.0,
        "model_calls": sum(record["model_calls"] for record in records),
        "elapsed_ms": round(sum(record["elapsed_ms"] for record in records), 3),
        "records": records,
    }


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload["cases"] if isinstance(payload, dict) else payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


async def _evaluate_extraction() -> dict[str, Any]:
    records = []
    for case in _load_cases(EXTRACTION_CASES):
        started = time.perf_counter()
        llm = ReplayLLM(ROOT / case["cassette"], inner=None, strict=True)
        record = _record(case["id"], elapsed_ms=0, model_calls=0)
        try:
            try:
                result = await extract_intent(case["question"], semantic_catalog(), llm=llm)
            except ExtractionError as exc:
                verdict = judge_extraction(case, error=exc)
            else:
                verdict = judge_extraction(case, intent=result.intent)
            record["ok"] = verdict.ok
            if verdict.ok and "expected" in verdict.details:
                record["expected"] = verdict.details["expected"]
            elif not verdict.ok:
                _fail(record, verdict.stage, verdict.code, verdict.message)
        except Exception as exc:
            if record["failure"] is None:
                _fail(record, "understanding", type(exc).__name__, "评测样本执行异常。")
        record["model_calls"] = llm.hits
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        records.append(record)
    return _suite("extraction", records)


async def _evaluate_clarification() -> dict[str, Any]:
    records = []
    extraction_cases = {case["id"]: case for case in _load_cases(EXTRACTION_CASES)}
    for case in _load_cases(CLARIFICATION_CASES):
        started = time.perf_counter()
        extraction_case = extraction_cases[case["extraction_case"]]
        llm = ReplayLLM(ROOT / extraction_case["cassette"], inner=None, strict=True)
        record = _record(case["id"], elapsed_ms=0, model_calls=0)
        try:
            result = await prepare_question(
                case["question"] if "question" in case else extraction_case["question"],
                semantic_catalog(),
                llm=llm,
            )
            expected_status = (
                "slots_complete" if case["expected_status"] == "slots_complete" else "needs_clarification"
            )
            if result.status != expected_status:
                _fail(record, "clarification", "STATUS_MISMATCH", "澄清状态与手写预期不一致。")
            elif (result.extraction is not None) != bool(case.get("expected_has_extraction")):
                _fail(
                    record,
                    "clarification",
                    "EXTRACTION_PRESENCE_MISMATCH",
                    "澄清结果中的抽取对象与预期不一致。",
                )
            elif tuple(result.missing_slots) != tuple(case.get("expected_missing_slots", ())):
                _fail(record, "clarification", "MISSING_SLOTS_MISMATCH", "缺失槽位与手写预期不一致。")
            elif _jsonable(asdict(result.clarification) if result.clarification else None) != case.get(
                "expected_clarification"
            ):
                _fail(record, "clarification", "CLARIFICATION_MISMATCH", "追问对象与手写预期不一致。")
            else:
                record["ok"] = True
        except Exception as exc:
            _fail(record, "clarification", type(exc).__name__, "澄清样本执行异常。")
        record["model_calls"] = llm.hits
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        records.append(record)
    return _suite("clarification", records)


def _golden_failure(outcome: CaseOutcome) -> tuple[str, str, str]:
    if outcome.compile_error:
        stage = outcome.compile_error.split("/", 1)[0]
        return (
            "semantic" if stage == "symbol_table" else "scope" if stage == "scope" else "compiler",
            outcome.compile_error,
            "黄金用例编译失败。",
        )
    if not outcome.matches:
        return ("compiler", "GOLDEN_MISMATCH", "编译产物与手写黄金 SQL 不一致。")
    if not outcome.validation_ok:
        return (
            "validation",
            outcome.validation_codes[0] if outcome.validation_codes else "VALIDATION_FAILED",
            "编译产物未通过 L4。",
        )
    return ("", "", "")


def _evaluate_golden() -> dict[str, Any]:
    records = []
    for case in load_cases(GOLDEN_DIR):
        started = time.perf_counter()
        outcome = evaluate_case(case, semantic_catalog(), validation_catalog())
        record = _record(case.id, elapsed_ms=(time.perf_counter() - started) * 1000, model_calls=0)
        record["ok"] = outcome.ok
        if not outcome.ok:
            stage, code, message = _golden_failure(outcome)
            _fail(record, stage, code, message)
        records.append(record)
    return _suite("golden", records)


async def _evaluate_execution() -> dict[str, Any]:
    started = time.perf_counter()
    record = _record("AQ-001", elapsed_ms=0, model_calls=0)
    with TemporaryDirectory(prefix="wenshu-eval-") as directory:
        root = Path(directory)
        business = root / "business.db"
        seed_business(business)
        llm = ReplayLLM(AQ_001_CASSETTE, inner=None, strict=True)
        service = QueryService(
            ClarificationService(store=MemorySessionStore(), llm=llm),
            semantic_catalog(),
            validation_catalog(),
            SQLiteExecutor(business),
        )
        case = {"expected": {"result": 20210000.0}}
        try:
            result = await service.start(
                AQ_001_QUESTION,
                owner_key="evaluation",
                catalog_revision="bank-mini-v1",
                scope=Scope.of([TABLE]),
            )
        except Exception as exc:
            verdict = judge_execution(case, error=exc)
        else:
            verdict = judge_execution(case, result)
        record["ok"] = verdict.ok
        if not verdict.ok:
            _fail(record, verdict.stage, verdict.code, verdict.message)
        record["model_calls"] = llm.hits
    record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return _suite("execution", [record])


def _coverage() -> dict[str, Any]:
    golden = load_cases(GOLDEN_DIR)
    eval_ids = load_eval_case_ids(EVAL_DIR)
    covered = {case.eval_case for case in golden if case.eval_case}
    return {
        "total": len(eval_ids),
        "covered": len(covered & set(eval_ids)),
        "missing": [case_id for case_id in eval_ids if case_id not in covered],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 离线评测报告", "", "- 模型模式：`replay`", "- 真实模型：否", "- 外部数据库：否", ""]
    lines += [
        "## 套件汇总",
        "",
        "| 套件 | 通过 | 总数 | 准确率 | 模型调用 | 耗时(ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for suite in report["suites"]:
        lines.append(
            f"| {suite['name']} | {suite['passed']} | {suite['total']} | {suite['accuracy']:.2%} | {suite['model_calls']} | {suite['elapsed_ms']:.3f} |"
        )
    lines += ["", "## 失败归因", ""]
    failures = [record for suite in report["suites"] for record in suite["records"] if not record["ok"]]
    if not failures:
        lines.append("无失败样本。")
    else:
        lines += ["| 样本 | 阶段 | 错误码 | 说明 |", "|---|---|---|---|"]
        lines.extend(
            f"| {record['id']} | {record['failure']['stage']} | {record['failure']['code']} | {record['failure']['message']} |"
            for record in failures
        )
    coverage = report["coverage"]
    lines += [
        "",
        "## 评测集覆盖",
        "",
        f"已覆盖 {coverage['covered']} / {coverage['total']} 条：`{', '.join(coverage['missing']) or '无'}`",
        "",
    ]
    return "\n".join(lines)


async def build_report(suites: set[str] | None = None) -> dict[str, Any]:
    selected = suites or {"extraction", "clarification", "golden", "execution"}
    results = []
    if "extraction" in selected:
        results.append(await _evaluate_extraction())
    if "clarification" in selected:
        results.append(await _evaluate_clarification())
    if "golden" in selected:
        results.append(_evaluate_golden())
    if "execution" in selected:
        results.append(await _evaluate_execution())
    return {
        "version": 1,
        "model_mode": "replay",
        "real_model": False,
        "external_database": False,
        "suites": results,
        "coverage": _coverage(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=Path("eval/report.json"))
    parser.add_argument("--markdown", type=Path, default=Path("eval/report.md"))
    parser.add_argument(
        "--suite", action="append", choices=("extraction", "clarification", "golden", "execution")
    )
    args = parser.parse_args(argv)
    report = asyncio.run(build_report(set(args.suite) if args.suite else None))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_render_markdown(report) + "\n", encoding="utf-8")
    failed = sum(suite["failed"] for suite in report["suites"])
    print(f"评测完成：{sum(suite['passed'] for suite in report['suites'])} 通过，{failed} 失败")
    print(f"JSON：{args.json}")
    print(f"Markdown：{args.markdown}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
