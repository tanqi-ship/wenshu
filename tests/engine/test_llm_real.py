"""真实客户端：思考模式参数、配置解析与工厂接线。

全部用 ``httpx.MockTransport`` 截获请求，不联网、不调用真实模型。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.llm import factory
from app.llm.protocol import Message
from app.llm.real import OpenAICompatLLM
from app.llm.replay import ReplayLLM


def _capturing_transport(captured: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    return httpx.MockTransport(handler)


def _expected_payload() -> dict:
    return {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "贷款余额"}],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stream": False,
    }


async def test_request_body_unchanged_when_thinking_is_unset() -> None:
    captured: list[dict] = []
    llm = OpenAICompatLLM(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        transport=_capturing_transport(captured),
    )
    await llm.complete([Message("user", "贷款余额")], max_tokens=2048)

    assert captured == [_expected_payload()]
    assert "thinking" not in captured[0]


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
async def test_thinking_field_is_added_when_set(mode: str) -> None:
    captured: list[dict] = []
    llm = OpenAICompatLLM(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        thinking=mode,
        transport=_capturing_transport(captured),
    )
    await llm.complete([Message("user", "贷款余额")], max_tokens=2048)

    assert captured == [{**_expected_payload(), "thinking": {"type": mode}}]


def test_settings_reads_thinking_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WENSHU_LLM_MODE", "fake")
    monkeypatch.delenv("WENSHU_LLM_THINKING", raising=False)
    assert Settings.from_env().llm_thinking == ""

    monkeypatch.setenv("WENSHU_LLM_THINKING", "disabled")
    assert Settings.from_env().llm_thinking == "disabled"

    monkeypatch.setenv("WENSHU_LLM_THINKING", "enabled")
    assert Settings.from_env().llm_thinking == "enabled"


def test_settings_rejects_unknown_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WENSHU_LLM_MODE", "fake")
    monkeypatch.setenv("WENSHU_LLM_THINKING", "adaptive")
    with pytest.raises(ValueError, match="WENSHU_LLM_THINKING"):
        Settings.from_env()


def test_factory_passes_thinking_to_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class StubClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "OpenAICompatLLM", StubClient)
    settings = Settings(
        llm_mode="real",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-flash",
        llm_thinking="disabled",
    )

    assert isinstance(factory.build_llm(settings), StubClient)
    assert captured["thinking"] == "disabled"


def test_factory_passes_thinking_to_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class StubClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "OpenAICompatLLM", StubClient)
    settings = Settings(
        llm_mode="real",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-flash",
        llm_thinking="disabled",
        llm_cassette_dir=tmp_path,
    )

    recorder = factory.build_recorder(settings, cassette="EX-001")
    assert isinstance(recorder, ReplayLLM)
    assert captured["thinking"] == "disabled"
