"""准备层CLI：澄清为正常结果，技术错误非零，始终离线。"""

import json
from pathlib import Path

import pytest
import yaml

from app.llm.fake import FakeLLM
from app.llm.protocol import LLMTransportError
from scripts import demo_extract, demo_prepare

CASES = yaml.safe_load(Path("tests/fixtures/clarification/cases.yaml").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_cli_normal_protocol_results_exit_zero(case, monkeypatch, capsys):
    monkeypatch.setenv("WENSHU_LLM_MODE", "real")

    def forbidden(*args, **kwargs):
        pytest.fail("离线演示禁止构造真实客户端")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.__init__", forbidden)
    assert demo_prepare.main(["--case", case["id"]]) == 0
    output = capsys.readouterr()
    assert not output.err
    banner, payload = output.out.split("\n", 1)
    assert "手写假响应" in banner
    assert "不代表可答" in banner
    data = json.loads(payload)
    assert data["status"] == case["expected_status"]
    assert data["missing_slots"] == case["expected_missing_slots"]
    assert data["clarification"] == case["expected_clarification"]
    assert (data["extraction"] is not None) == case["expected_has_extraction"]
    if not case["expected_has_extraction"]:
        assert data["extraction"] is None  # 无伪造Completion或token统计。
    else:
        assert data["extraction"]["completion"]["model"] == "synthetic/handwritten-fixture"


def test_default_is_first_case(capsys):
    assert demo_prepare.main([]) == 0
    assert '"id": "EX-001"' in capsys.readouterr().out


def test_expected_fields_are_not_implementation_branch_table(tmp_path, capsys):
    # 故意写错expected，CLI仍须执行抽取/检查，而非输出标准答案。
    (tmp_path / "cases.yaml").write_text(
        "cases: [{id: CUSTOM, extraction_case: EX-008, expected_status: slots_complete, expected_clarification: null}]",
        encoding="utf-8",
    )
    assert demo_prepare.main(["--case", "CUSTOM", "--cases-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    data = json.loads(output.split("\n", 1)[1])
    assert data["id"] == "CUSTOM"
    assert data["status"] == "needs_clarification"
    assert data["clarification"]["slot"] == "time_range"


def test_cli_replay_miss_visible(tmp_path, capsys):
    (tmp_path / "cases.yaml").write_text(
        "cases: [{id: EX-001, question: 新问题, cassette: 不存在.json}]", encoding="utf-8"
    )
    assert demo_prepare.main(["--extraction-cases-dir", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "ReplayMiss" in output.err
    assert '"status"' not in output.out


@pytest.mark.parametrize(
    "response",
    [
        "bad json",
        '{"metrics":[],"unresolved":[],"scope":{}}',
        '{"metrics":[],"unresolved":[],"time":{"grain":"month_end","points":["2026-04-29"]}}',
    ],
)
def test_cli_extraction_failures_not_clarification(response, monkeypatch, capsys):
    llm = FakeLLM([response])
    monkeypatch.setattr(demo_prepare, "ReplayLLM", lambda *args, **kwargs: llm)
    assert demo_prepare.main([]) == 1
    output = capsys.readouterr()
    assert "EXTRACTION_" in output.err
    assert '"status"' not in output.out
    assert llm.call_count == 1


def test_cli_transport_failure_no_retry(monkeypatch, capsys):
    class Failing:
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise LLMTransportError("offline transport failure")

    llm = Failing()
    monkeypatch.setattr(demo_prepare, "ReplayLLM", lambda *args, **kwargs: llm)
    assert demo_prepare.main([]) == 1
    assert "LLMTransportError" in capsys.readouterr().err
    assert llm.calls == 1


@pytest.mark.parametrize("case_id", ["EX-010", "EX-011"])
def test_old_extract_cli_errors_still_nonzero(case_id, capsys):
    assert demo_extract.main(["--case", case_id]) == 1
    assert "EXTRACTION_" in capsys.readouterr().err
