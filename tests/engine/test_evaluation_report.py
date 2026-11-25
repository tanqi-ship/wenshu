import asyncio
import json

from scripts.evaluate import _render_markdown, build_report


def test_golden_report_suite_has_thirty_expected_passes():
    report = asyncio.run(build_report({"golden"}))
    suite = report["suites"][0]
    assert suite["name"] == "golden"
    assert (suite["total"], suite["passed"], suite["failed"]) == (30, 30, 0)
    assert report["coverage"] == {
        "total": 10,
        "covered": 8,
        "missing": ["AQ-007", "AQ-010"],
    }


def test_full_report_is_replay_only_and_serializable():
    report = asyncio.run(build_report())
    assert report["model_mode"] == "replay"
    assert report["real_model"] is False
    assert report["external_database"] is False
    assert sum(suite["total"] for suite in report["suites"]) == 71
    assert json.loads(json.dumps(report, ensure_ascii=False))["suites"]


def test_markdown_reports_failures_by_stage():
    report = {
        "suites": [
            {
                "name": "test",
                "passed": 0,
                "total": 1,
                "accuracy": 0,
                "model_calls": 1,
                "elapsed_ms": 1.0,
                "records": [
                    {
                        "id": "EX-999",
                        "ok": False,
                        "failure": {"stage": "understanding", "code": "INTENT_MISMATCH", "message": "不一致"},
                    }
                ],
            }
        ],
        "coverage": {"covered": 0, "total": 0, "missing": []},
    }
    markdown = _render_markdown(report)
    assert "EX-999" in markdown
    assert "understanding" in markdown
    assert "INTENT_MISMATCH" in markdown
