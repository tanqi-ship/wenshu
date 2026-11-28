"""真实模型运行器的前置检查、预算、汇总与输出。

全部用按问题返回手写 cassette 文本的假客户端，不联网、不调用真实模型。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.llm.protocol import Completion, Message
from scripts.evaluate_real import (
    CONCLUSION_BOUNDARY,
    REAL_DIR,
    _exclusion_reason,
    run,
    summarize_suite,
)

ROOT = Path(__file__).resolve().parents[2]
EX_001_CASSETTE = ROOT / "tests/cassettes/intent_extraction/EX-001.json"
EX_006_CASSETTE = ROOT / "tests/cassettes/intent_extraction/EX-006.json"
AQ_001_CASSETTE = ROOT / "tests/cassettes/query_execution/AQ-001.json"
EX_001_QUESTION = "截至2026年4月末全行贷款余额"
AQ_001_QUESTION = "2026年4月末全行贷款余额是多少？"
AQ_006_QUESTION = "从报表查询2026年4月各产品的不良贷款率是多少？"
AQ_008_QUESTION = "2026年1月到4月，每个月贷款余额最高的支行是哪个、余额多少？"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_mode": "real",
        "llm_api_key": "sk-test-secret",
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-flash",
        "llm_thinking": "disabled",
    }
    values.update(overrides)
    return Settings(**values)


def _cassette_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(next(iter(payload["entries"].values()))["text"])


class _QuestionLLM:
    """按问题返回手写 cassette 文本；消息中找不到问题就直接报错。"""

    def __init__(self, responses: dict[str, str], *, model: str = "fake/deepseek-flash") -> None:
        self._responses = responses
        self._model = model
        self.call_count = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        self.call_count += 1
        content = messages[-1].content
        for question, text in self._responses.items():
            if question in content:
                return Completion(
                    text=text,
                    model=self._model,
                    prompt_tokens=5,
                    completion_tokens=7,
                    meta={"finish_reason": "stop"},
                )
        raise AssertionError(f"未配置的问题：{content[:80]}")


class _SpyFactory:
    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, settings: Settings) -> _QuestionLLM:
        self.call_count += 1
        raise AssertionError("前置检查失败时不应构造客户端")


def _factory(inner: _QuestionLLM):
    return lambda settings: inner


async def test_extraction_cases_path_is_recorded_and_used(tmp_path: Path) -> None:
    cases_file = tmp_path / "blind.yaml"
    cases_file.write_text(
        "cases:\n"
        "  - id: BL-X\n"
        "    question: 截止2026年4月末，贷款余额\n"
        "    expected: {metric: 贷款余额, time: {grain: month_end, points: ['2026-04-30']}}\n"
        "    compile: true\n",
        encoding="utf-8",
    )
    llm = _QuestionLLM({"截止2026年4月末，贷款余额": _cassette_text(EX_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["BL-X"],
        output_root=tmp_path,
        run_id="blind",
        client_factory=_factory(llm),
        extraction_cases_path=cases_file,
    )
    assert code == 0
    report = json.loads((tmp_path / "blind" / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["extraction_cases_path"] == str(cases_file)
    assert (
        report["metadata"]["extraction_cases_sha256"] == hashlib.sha256(cases_file.read_bytes()).hexdigest()
    )
    assert report["suites"][0]["attempts_detail"][0]["case_id"] == "BL-X"


async def test_preflight_rejects_replay_without_calling_factory(tmp_path: Path) -> None:
    spy = _SpyFactory()
    code = await run(
        _settings(llm_mode="replay", llm_api_key=""),
        output_root=tmp_path,
        run_id="replay",
        client_factory=spy,
    )
    assert code == 2
    assert spy.call_count == 0
    assert not (tmp_path / "replay").exists()


async def test_preflight_rejects_empty_key(tmp_path: Path) -> None:
    spy = _SpyFactory()
    code = await run(
        _settings(llm_api_key=""),
        output_root=tmp_path,
        run_id="empty-key",
        client_factory=spy,
    )
    assert code == 2
    assert spy.call_count == 0


async def test_preflight_rejects_thinking_not_disabled(tmp_path: Path) -> None:
    spy = _SpyFactory()
    code = await run(
        _settings(llm_thinking=""),
        output_root=tmp_path,
        run_id="thinking",
        client_factory=spy,
    )
    assert code == 2
    assert spy.call_count == 0


async def test_preflight_rejects_existing_output_dir(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    spy = _SpyFactory()
    code = await run(
        _settings(),
        output_root=tmp_path,
        run_id="existing",
        client_factory=spy,
    )
    assert code == 2
    assert spy.call_count == 0


async def test_budget_exhausted_returns_three_and_keeps_partial_results(tmp_path: Path) -> None:
    inner = _QuestionLLM({EX_001_QUESTION: _cassette_text(EX_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=3,
        case_ids=["EX-001"],
        max_calls=2,
        output_root=tmp_path,
        run_id="budget",
        client_factory=_factory(inner),
    )

    assert code == 3
    directory = tmp_path / "budget"
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert report["metadata"]["budget_exhausted"] is True
    assert report["metadata"]["status"] == "budget_exhausted"
    assert len(report["suites"][0]["attempts_detail"]) == 2
    assert inner.call_count == 2
    assert len((directory / "calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2


async def test_all_correct_returns_zero(tmp_path: Path) -> None:
    inner = _QuestionLLM({EX_001_QUESTION: _cassette_text(EX_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=3,
        case_ids=["EX-001"],
        max_calls=20,
        output_root=tmp_path,
        run_id="all-correct",
        client_factory=_factory(inner),
    )

    assert code == 0
    report = json.loads((tmp_path / "all-correct/report.json").read_text(encoding="utf-8"))
    suite = report["suites"][0]
    assert report["status"] == "ok"
    assert suite["total_cases"] == 1
    assert suite["completed_cases"] == 1
    assert suite["full_pass_rate"] == 1.0
    assert suite["majority_pass_rate"] == 1.0
    assert suite["unstable_cases"] == []


async def test_execution_case_records_golden_match(tmp_path: Path) -> None:
    inner = _QuestionLLM({AQ_001_QUESTION: _cassette_text(AQ_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["AQ-001"],
        max_calls=10,
        output_root=tmp_path,
        run_id="execution",
        client_factory=_factory(inner),
    )

    assert code == 0
    report = json.loads((tmp_path / "execution/report.json").read_text(encoding="utf-8"))
    execution = report["suites"][1]
    assert execution["total_cases"] == 1
    assert execution["attempts_detail"][0]["golden_matches"] is True


async def test_aq006_report_case_executes_on_sqlite(tmp_path: Path) -> None:
    inner = _QuestionLLM({AQ_006_QUESTION: _cassette_text(EX_006_CASSETTE)})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["AQ-006"],
        max_calls=10,
        output_root=tmp_path,
        run_id="aq006",
        client_factory=_factory(inner),
    )

    assert code == 0
    report = json.loads((tmp_path / "aq006/report.json").read_text(encoding="utf-8"))
    execution = report["suites"][1]
    attempt = execution["attempts_detail"][0]
    assert attempt["ok"]
    assert attempt["golden_matches"] is True


async def test_aq008_explicit_range_case_executes(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "metrics": ["贷款余额"],
            "unresolved": [],
            "time": {
                "grain": "month",
                "start": "2026-01-01",
                "end": "2026-04-30",
            },
            "dimensions": ["机构"],
            "filters": [],
            "comparison": None,
            "top_n": 1,
            "top_n_grain": "month",
            "source_mode": "plain",
            "share": None,
        },
        ensure_ascii=False,
    )
    inner = _QuestionLLM({AQ_008_QUESTION: response})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["AQ-008"],
        max_calls=10,
        output_root=tmp_path,
        run_id="aq008",
        client_factory=_factory(inner),
    )

    assert code == 0
    report = json.loads((tmp_path / "aq008/report.json").read_text(encoding="utf-8"))
    execution = report["suites"][1]
    attempt = execution["attempts_detail"][0]
    assert attempt["ok"]
    assert attempt["golden_matches"] is True


async def test_excluded_case_is_reported_and_not_counted(tmp_path: Path) -> None:
    inner = _QuestionLLM({AQ_001_QUESTION: _cassette_text(AQ_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["AQ-001", "AQ-007"],
        max_calls=10,
        output_root=tmp_path,
        run_id="excluded",
        client_factory=_factory(inner),
    )

    assert code == 0
    report = json.loads((tmp_path / "excluded/report.json").read_text(encoding="utf-8"))
    execution = report["suites"][1]
    assert execution["total_cases"] == 2
    assert execution["completed_cases"] == 1
    assert execution["excluded_cases"] == [
        {
            "suite": "execution",
            "case_id": "AQ-007",
            "reason": "演示语义层没有核销指标，单 Intent 无法产出两列结果",
        }
    ]
    assert len(execution["attempts_detail"]) == 1
    markdown = (tmp_path / "excluded/report.md").read_text(encoding="utf-8")
    assert "排除题" in markdown
    assert "AQ-007" in markdown


def test_measurement_status_is_fail_closed() -> None:
    with pytest.raises(ValueError, match=r"measurement\.status"):
        _exclusion_reason({"id": "X", "measurement": {"status": "skip", "reason": "原因"}})
    with pytest.raises(ValueError, match=r"measurement\.reason"):
        _exclusion_reason({"id": "X", "measurement": {"status": "excluded", "reason": ""}})


async def test_execution_extraction_error_is_attributed_to_understanding(tmp_path: Path) -> None:
    invalid = json.dumps(
        {
            "metrics": ["贷款余额"],
            "unresolved": ["2026年4月末（未确认）"],
            "time": None,
            "dimensions": [],
            "filters": [],
            "comparison": None,
            "top_n": None,
            "top_n_grain": None,
            "source_mode": "plain",
            "share": None,
        },
        ensure_ascii=False,
    )
    inner = _QuestionLLM({AQ_001_QUESTION: invalid})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["AQ-001"],
        max_calls=10,
        output_root=tmp_path,
        run_id="execution-error",
        client_factory=_factory(inner),
    )

    assert code == 1
    report = json.loads((tmp_path / "execution-error/report.json").read_text(encoding="utf-8"))
    attempt = report["suites"][1]["attempts_detail"][0]
    assert attempt["stage"] == "understanding"
    assert attempt["code"] == "EXTRACTION_SCHEMA_INVALID"


def test_summary_counts_full_majority_unstable_and_transport() -> None:
    attempts: list[dict[str, Any]] = []
    attempts += [{"case_id": "A", "attempt": n, "ok": True, "transport": False} for n in (1, 2, 3)]
    attempts += [
        {"case_id": "B", "attempt": 1, "ok": True, "transport": False},
        {"case_id": "B", "attempt": 2, "ok": True, "transport": False},
        {"case_id": "B", "attempt": 3, "ok": False, "transport": False},
    ]
    attempts += [{"case_id": "C", "attempt": n, "ok": False, "transport": False} for n in (1, 2, 3)]
    attempts += [
        {"case_id": "D", "attempt": 1, "ok": True, "transport": False},
        {"case_id": "D", "attempt": 2, "ok": True, "transport": False},
        {"case_id": "D", "attempt": 3, "ok": False, "transport": True},
    ]

    summary = summarize_suite("test", attempts, 3)

    assert summary["total_cases"] == 4
    assert summary["completed_cases"] == 3
    assert summary["incomplete_cases"] == ["D"]
    assert summary["full_pass_cases"] == 1
    assert summary["full_pass_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert summary["majority_pass_cases"] == 2
    assert summary["majority_pass_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert summary["unstable_cases"] == ["B"]
    assert summary["transport_attempts"] == 1
    assert summary["counted_attempts"] == 11
    assert summary["attempt_accuracy"] == pytest.approx(7 / 11, abs=1e-6)


async def test_markdown_has_boundary_and_outputs_never_contain_key(tmp_path: Path) -> None:
    secret = "sk-never-write-this-secret"
    inner = _QuestionLLM({EX_001_QUESTION: _cassette_text(EX_001_CASSETTE)})
    code = await run(
        _settings(llm_api_key=secret),
        repeats=1,
        case_ids=["EX-001"],
        max_calls=10,
        output_root=tmp_path,
        run_id="secret",
        client_factory=_factory(inner),
    )

    assert code == 0
    directory = tmp_path / "secret"
    for path in directory.iterdir():
        assert secret not in path.read_text(encoding="utf-8")
    assert CONCLUSION_BOUNDARY in (directory / "report.md").read_text(encoding="utf-8")
    assert "transport 失败不计入准确率分母" in (directory / "report.md").read_text(encoding="utf-8")


def _tree_hash(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.json"))
    }


async def test_cassettes_are_unchanged(tmp_path: Path) -> None:
    cassette_root = ROOT / "tests/cassettes"
    before = _tree_hash(cassette_root)
    inner = _QuestionLLM({EX_001_QUESTION: _cassette_text(EX_001_CASSETTE)})
    code = await run(
        _settings(),
        repeats=1,
        case_ids=["EX-001"],
        max_calls=10,
        output_root=tmp_path,
        run_id="cassettes",
        client_factory=_factory(inner),
    )

    assert code == 0
    assert _tree_hash(cassette_root) == before


async def test_run_directory_does_not_appear_in_git_status() -> None:
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"test-{uuid.uuid4().hex}"
    run_dir = REAL_DIR / run_id
    inner = _QuestionLLM({EX_001_QUESTION: _cassette_text(EX_001_CASSETTE)})
    try:
        code = await run(
            _settings(),
            repeats=1,
            case_ids=["EX-001"],
            max_calls=10,
            output_root=REAL_DIR,
            run_id=run_id,
            client_factory=_factory(inner),
        )
        assert code == 0
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert f"eval/real/{run_id}" not in status
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
        with suppress(OSError):
            REAL_DIR.rmdir()
