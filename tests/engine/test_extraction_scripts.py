"""CLI 保持离线、失败可见；录制接线只用假客户端测试。"""

import json

import pytest

from app.config import Settings
from app.engine.understanding import ExtractionError
from app.llm.fake import FakeLLM
from app.llm.replay import ReplayLLM
from scripts import demo_extract, record_cassette


def test_demo_offline_even_real_env(monkeypatch, capsys):
    monkeypatch.setenv("WENSHU_LLM_MODE", "real")

    def forbidden(*args, **kwargs):
        pytest.fail("demo 不允许构造真实客户端")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.__init__", forbidden)
    assert demo_extract.main(["--case", "EX-001"]) == 0
    output = capsys.readouterr().out
    assert "贷款余额" in output
    assert "手写假响应" in output
    assert "synthetic/handwritten-fixture" in output


@pytest.mark.parametrize(
    "case,code", [("EX-010", "EXTRACTION_MULTI_METRIC_UNSUPPORTED"), ("EX-011", "EXTRACTION_UNRESOLVED")]
)
def test_demo_failures(case, code, capsys):
    assert demo_extract.main(["--case", case]) == 1
    assert code in capsys.readouterr().err


def test_demo_miss_visible(tmp_path, capsys):
    manifest = tmp_path / "cases.yaml"
    manifest.write_text("cases: [{id: MISS, question: 没录制, cassette: 不存在.json}]", encoding="utf-8")
    assert demo_extract.main(["--case", "MISS", "--cases-dir", str(tmp_path)]) == 1
    assert "ReplayMiss" in capsys.readouterr().err


@pytest.mark.parametrize(
    "text,valid", [('{"metrics":["贷款余额"],"unresolved":[]}', True), ("坏响应", False)]
)
async def test_recorder_flush_even_parse_failure(tmp_path, text, valid):
    target = tmp_path / "recording.json"
    llm = ReplayLLM(target, inner=FakeLLM([text]), strict=False)
    if valid:
        result = await record_cassette.record_question("贷款余额", llm)
        assert result.intent.metric == "贷款余额"
    else:
        with pytest.raises(ExtractionError):
            await record_cassette.record_question("贷款余额", llm)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert next(iter(payload["entries"].values()))["text"] == text


def test_record_requires_explicit_real(monkeypatch, capsys):
    monkeypatch.setattr(record_cassette.Settings, "from_env", lambda: Settings(llm_mode="replay"))
    assert record_cassette.main(["--case", "EX-001"]) == 2
    assert "真实模型" in capsys.readouterr().err


@pytest.mark.parametrize("text,exit_code", [('{"metrics":["贷款余额"],"unresolved":[]}', 0), ("坏响应", 1)])
def test_record_cli_injected_only(tmp_path, monkeypatch, text, exit_code):
    settings = Settings(
        llm_mode="real",
        llm_base_url="https://not-used.invalid",
        llm_model="synthetic",
        llm_api_key="not-a-real-key",
        llm_cassette_dir=tmp_path,
    )
    monkeypatch.setattr(record_cassette.Settings, "from_env", lambda: settings)

    def build(settings, *, cassette):
        assert cassette.startswith("recorded/intent_extraction/")
        return ReplayLLM(
            settings.llm_cassette_dir / (cassette + ".json"), inner=FakeLLM([text]), strict=False
        )

    monkeypatch.setattr(record_cassette, "build_recorder", build)
    assert (
        record_cassette.main(["--case", "EX-001", "--cases-dir", "tests/fixtures/intent_extraction"])
        == exit_code
    )
    target = tmp_path / "recorded/intent_extraction/EX-001.json"
    assert target.exists()
    metadata = json.loads(target.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    assert metadata["provenance"] == "recorded_real"
    assert metadata["extraction_ok"] is (exit_code == 0)
