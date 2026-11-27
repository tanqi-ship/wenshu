"""记录型模型客户端与调用预算。

只做观测，不重试、不改请求：包装后传给内层客户端的消息、温度、token 上限和超时
与不包装时完全一致。记录用于真实模型基线的 ``calls.jsonl``。

预算规则：
- 成功或失败的调用尝试都计入 ``max_calls``；触顶后不再调用内层。
- 只有 ``LLMTransportError`` 计入连续失败；成功一次后清零。
- 连续失败达到上限时抛 ``RunAborted``；单次失败仍原样抛 ``LLMTransportError``。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from app.llm.protocol import Completion, LLMClient, LLMTransportError, Message
from app.llm.replay import message_key


class BudgetExhausted(Exception):
    """调用数达到预算，未发起本次调用。"""


class RunAborted(Exception):
    """连续 transport 失败达到上限，中止本轮运行。"""


@dataclass(frozen=True, slots=True)
class CallRecord:
    """一次模型调用的观测记录；成功与失败都保留。"""

    index: int
    messages: tuple[tuple[str, str], ...]
    message_digest: str
    message_summary: str
    elapsed_ms: float
    text: str = ""
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_code: str | None = None
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _summary(messages: Sequence[Message], *, limit: int = 200) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content[:limit]
    return ""


class RecordingLLM:
    """包住任意 ``LLMClient``，记录每次调用并约束预算。"""

    def __init__(
        self,
        inner: LLMClient,
        *,
        max_calls: int = 200,
        max_consecutive_transport_failures: int = 5,
    ) -> None:
        if type(max_calls) is not int or max_calls <= 0:
            raise ValueError("max_calls 必须是正整数")
        if type(max_consecutive_transport_failures) is not int or max_consecutive_transport_failures <= 0:
            raise ValueError("max_consecutive_transport_failures 必须是正整数")
        self._inner = inner
        self._max_calls = max_calls
        self._max_consecutive = max_consecutive_transport_failures
        self._records: list[CallRecord] = []
        self._consecutive_transport_failures = 0

    @property
    def calls(self) -> list[CallRecord]:
        return list(self._records)

    @property
    def call_count(self) -> int:
        return len(self._records)

    @property
    def consecutive_transport_failures(self) -> int:
        return self._consecutive_transport_failures

    def _record(
        self,
        messages: Sequence[Message],
        started: float,
        *,
        completion: Completion | None = None,
        error: Exception | None = None,
        error_code: str | None = None,
    ) -> None:
        self._records.append(
            CallRecord(
                index=len(self._records) + 1,
                messages=tuple((message.role, message.content) for message in messages),
                message_digest=message_key(messages),
                message_summary=_summary(messages),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                text=completion.text if completion is not None else "",
                model=completion.model if completion is not None else "",
                finish_reason=(
                    str(completion.meta.get("finish_reason", "")) if completion is not None else ""
                ),
                prompt_tokens=completion.prompt_tokens if completion is not None else 0,
                completion_tokens=completion.completion_tokens if completion is not None else 0,
                error_code=error_code,
                error=f"{type(error).__name__}: {error}" if error is not None else None,
            )
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        if self._consecutive_transport_failures >= self._max_consecutive:
            raise RunAborted(f"连续 {self._consecutive_transport_failures} 次 transport 失败，中止本轮。")
        if self.call_count >= self._max_calls:
            raise BudgetExhausted(f"模型调用数达到预算 {self._max_calls} 次，停止调用。")

        started = time.perf_counter()
        try:
            completion = await self._inner.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )
        except LLMTransportError as exc:
            self._record(messages, started, error=exc, error_code="transport")
            self._consecutive_transport_failures += 1
            if self._consecutive_transport_failures >= self._max_consecutive:
                raise RunAborted(
                    f"连续 {self._consecutive_transport_failures} 次 transport 失败，中止本轮。"
                ) from exc
            raise
        except Exception as exc:
            self._record(messages, started, error=exc, error_code=type(exc).__name__)
            raise

        self._consecutive_transport_failures = 0
        self._record(messages, started, completion=completion)
        return completion
