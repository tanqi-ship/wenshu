"""记录型客户端：记录内容、原样转发、预算与连续失败中止。

全部包住 fake 客户端，不联网、不调用真实模型。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.llm.fake import FakeLLM
from app.llm.protocol import (
    Completion,
    LLMTransportError,
    Message,
    canonical_messages,
)
from app.llm.replay import message_key
from scripts.call_recorder import BudgetExhausted, RecordingLLM, RunAborted


class _OutcomeLLM:
    """按预置结果返回或抛错，用来模拟 transport 失败与成功交替。"""

    def __init__(self, outcomes: Sequence[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        self.calls.append(canonical_messages(messages))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, Completion)
        return outcome


async def test_records_success_and_forwards_request() -> None:
    messages = [Message("system", "系统"), Message("user", "贷款余额是多少")]
    inner = FakeLLM(["回答"])
    recorder = RecordingLLM(inner, max_calls=10)

    completion = await recorder.complete(messages, temperature=0.2, max_tokens=123)

    assert completion.text == "回答"
    assert inner.call_count == 1
    assert recorder.call_count == 1
    record = recorder.calls[0]
    assert record.index == 1
    assert record.messages == (("system", "系统"), ("user", "贷款余额是多少"))
    assert record.message_digest == message_key(messages)
    assert record.message_summary == "贷款余额是多少"
    assert record.text == "回答"
    assert record.model == "fake"
    assert record.prompt_tokens == 0
    assert record.completion_tokens == 0
    assert record.finish_reason == ""
    assert record.error is None
    assert record.error_code is None
    assert record.elapsed_ms >= 0


async def test_request_is_identical_when_wrapped() -> None:
    messages = [Message("user", "问题")]
    plain = FakeLLM(["回答"])
    await plain.complete(messages, temperature=0.4, max_tokens=99, timeout_s=30)

    inner = FakeLLM(["回答"])
    recorder = RecordingLLM(inner, max_calls=10)
    await recorder.complete(messages, temperature=0.4, max_tokens=99, timeout_s=30)

    assert inner.calls == plain.calls


async def test_records_model_tokens_and_finish_reason() -> None:
    messages = [Message("user", "问题")]
    completion = Completion(
        text="回答",
        model="deepseek-flash",
        prompt_tokens=11,
        completion_tokens=22,
        meta={"finish_reason": "stop"},
    )
    inner = _OutcomeLLM([completion])
    recorder = RecordingLLM(inner, max_calls=10)

    await recorder.complete(messages)

    record = recorder.calls[0]
    assert record.model == "deepseek-flash"
    assert (record.prompt_tokens, record.completion_tokens) == (11, 22)
    assert record.total_tokens == 33
    assert record.finish_reason == "stop"


async def test_transport_error_is_recorded_and_reraised() -> None:
    error = LLMTransportError("模型调用超时")
    recorder = RecordingLLM(_OutcomeLLM([error]), max_calls=10)

    with pytest.raises(LLMTransportError, match="模型调用超时"):
        await recorder.complete([Message("user", "问题")])

    assert recorder.call_count == 1
    record = recorder.calls[0]
    assert record.error_code == "transport"
    assert record.error is not None and "模型调用超时" in record.error
    assert recorder.consecutive_transport_failures == 1


async def test_budget_stops_before_calling_inner() -> None:
    inner = FakeLLM([], default="回答")
    recorder = RecordingLLM(inner, max_calls=2)

    await recorder.complete([Message("user", "一")])
    await recorder.complete([Message("user", "二")])
    with pytest.raises(BudgetExhausted, match="预算"):
        await recorder.complete([Message("user", "三")])

    assert inner.call_count == 2
    assert recorder.call_count == 2


async def test_five_consecutive_transport_failures_abort() -> None:
    errors = [LLMTransportError(f"失败 {index}") for index in range(1, 6)]
    recorder = RecordingLLM(_OutcomeLLM(errors), max_calls=10)

    for index in range(1, 5):
        with pytest.raises(LLMTransportError, match=f"失败 {index}"):
            await recorder.complete([Message("user", "问题")])

    with pytest.raises(RunAborted, match="连续 5 次"):
        await recorder.complete([Message("user", "问题")])

    assert recorder.call_count == 5
    assert recorder.consecutive_transport_failures == 5
    assert recorder.calls[-1].error_code == "transport"

    with pytest.raises(RunAborted):
        await recorder.complete([Message("user", "问题")])


async def test_success_resets_consecutive_transport_failures() -> None:
    outcomes: list[object] = [LLMTransportError("失败") for _ in range(4)]
    outcomes.append(Completion(text="成功", model="fake"))
    outcomes.extend(LLMTransportError("失败") for _ in range(4))
    recorder = RecordingLLM(_OutcomeLLM(outcomes), max_calls=20, max_consecutive_transport_failures=5)

    for _ in range(4):
        with pytest.raises(LLMTransportError):
            await recorder.complete([Message("user", "问题")])
    await recorder.complete([Message("user", "问题")])
    assert recorder.consecutive_transport_failures == 0

    for _ in range(4):
        with pytest.raises(LLMTransportError):
            await recorder.complete([Message("user", "问题")])
    assert recorder.consecutive_transport_failures == 4
    assert recorder.call_count == 9
