"""双引擎回归门禁的判定逻辑。

这组测试保护的是"重构不会越改越差"这条底线 —— 如果门禁本身的判定写错了，
那它比没有门禁更危险：会给出虚假的安全感。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_regression import (
    CaseOutcome,
    Report,
    compare,
    has_blocking,
    render_summary,
)


def _report(engine: str, **cases: tuple[bool, str]) -> Report:
    report = Report(engine=engine)
    for case_id, (correct, answer_path) in cases.items():
        report.cases[case_id] = CaseOutcome(
            case_id=case_id,
            correct=correct,
            answer_path=answer_path,
            duration_ms=1000,
            llm_calls=3,
        )
    return report


class TestRegressionDetection:
    def test_old_correct_new_wrong_is_blocking(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "etl_calculated"))
        current = _report("wenshu", AQ_001=(False, "no_reference"))
        findings = compare(baseline, current)
        assert any(f.kind == "REGRESSION" for f in findings)
        assert has_blocking(findings)

    def test_old_wrong_new_correct_is_improvement_not_blocking(self) -> None:
        baseline = _report("legacy", AQ_001=(False, "no_reference"))
        current = _report("wenshu", AQ_001=(True, "etl_calculated"))
        findings = compare(baseline, current)
        assert [f.kind for f in findings] == ["IMPROVEMENT"]
        assert not has_blocking(findings)

    def test_missing_case_is_blocking(self) -> None:
        # 用真实 ID 格式（带连字符），确认报告里缺的用例能被正确指认
        baseline = _report("legacy", **{"AQ-001": (True, "x"), "AQ-002": (True, "y")})
        current = _report("wenshu", **{"AQ-001": (True, "x")})
        findings = compare(baseline, current)
        missing = [f for f in findings if f.kind == "MISSING"]
        assert [f.case_id for f in missing] == ["AQ-002"]
        assert has_blocking(findings)

    def test_identical_reports_have_no_findings(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "x"), AQ_002=(True, "y"))
        current = _report("wenshu", AQ_001=(True, "x"), AQ_002=(True, "y"))
        assert compare(baseline, current) == []


class TestAccuracyDrop:
    def test_drop_within_limit_passes(self) -> None:
        # 100% -> 99%：降 1pp，低于 2pp 上限
        baseline = _report("legacy", **{f"AQ_{i:03d}": (True, "x") for i in range(100)})
        cases = {f"AQ_{i:03d}": (i != 0, "x") for i in range(100)}
        current = _report("wenshu", **cases)
        findings = compare(baseline, current)
        assert not any(f.kind == "DROP" for f in findings)

    def test_drop_beyond_limit_blocks(self) -> None:
        # 100% -> 90%：降 10pp
        baseline = _report("legacy", **{f"AQ_{i:03d}": (True, "x") for i in range(10)})
        cases = {f"AQ_{i:03d}": (i == 0, "x") for i in range(10)}
        current = _report("wenshu", **cases)
        findings = compare(baseline, current)
        assert any(f.kind == "DROP" for f in findings)
        assert has_blocking(findings)


class TestCostAndLatency:
    def test_cost_growth_warns_but_does_not_block(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "x"))
        current = _report("wenshu", AQ_001=(True, "x"))
        current.cases["AQ_001"].llm_calls = 10  # 3 -> 10，+233%
        findings = compare(baseline, current)
        assert any(f.kind == "COST_UP" for f in findings)
        assert not has_blocking(findings)

    def test_slower_warns_but_does_not_block(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "x"))
        current = _report("wenshu", AQ_001=(True, "x"))
        current.cases["AQ_001"].duration_ms = 5000
        findings = compare(baseline, current)
        assert any(f.kind == "SLOWER" for f in findings)
        assert not has_blocking(findings)


class TestReportIO:
    def test_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "report.json"
        path.write_text(
            json.dumps(
                {
                    "engine": "legacy",
                    "cases": {
                        "AQ-001": {
                            "correct": True,
                            "answer_path": "etl_calculated",
                            "duration_ms": 1200,
                            "llm_calls": 4,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        report = Report.load(path)
        assert report.engine == "legacy"
        assert report.accuracy == pytest.approx(100.0)
        assert report.total_llm_calls == 4
        assert report.cases["AQ-001"].answer_path == "etl_calculated"

    def test_empty_report_accuracy_is_zero(self) -> None:
        assert Report().accuracy == 0.0


class TestSummaryRendering:
    def test_summary_contains_both_engines(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "x"))
        current = _report("wenshu", AQ_001=(False, "y"))
        summary = render_summary(baseline, current, compare(baseline, current))
        assert "legacy" in summary
        assert "wenshu" in summary
        assert "REGRESSION" in summary

    def test_summary_with_no_findings(self) -> None:
        baseline = _report("legacy", AQ_001=(True, "x"))
        current = _report("wenshu", AQ_001=(True, "x"))
        summary = render_summary(baseline, current, [])
        assert "无差异" in summary
