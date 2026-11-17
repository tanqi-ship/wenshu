"""确定性假模型。

用途：单元测试。断言"给了这些输入，引擎的后续行为是否正确"。

两条纪律：

1. **脚本用尽就抛错**，不许返回空串或编造内容。
   一个悄悄返回空字符串的 fake，会让测试变成"什么都没验但显示通过"。
2. **记录每次调用**，测试可以断言调用次数与顺序 ——
   这对"澄清预算""重试上限"这类约束是必需的。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from app.llm.protocol import (
    Completion,
    LLMProtocolError,
    Message,
    canonical_messages,
)


class ScriptExhausted(LLMProtocolError):
    """脚本已用尽却仍在调用。

    出现它通常意味着两种好事之一：引擎多调了一次模型（bug），
    或者测试脚本写少了（测试要补）。
    """


@dataclass(slots=True)
class _Step:
    """一条脚本项。

    ``match`` 是正则，用来匹配最后一条 user 消息的内容。
    ``None`` 表示无条件匹配（按顺序消费）。
    """

    response: str
    match: re.Pattern[str] | None = None


@dataclass(slots=True)
class Call:
    """一次调用的记录，供测试断言。"""

    messages: list[dict[str, str]]
    temperature: float
    max_tokens: int

    @property
    def last_user_content(self) -> str:
        for message in reversed(self.messages):
            if message["role"] == "user":
                return message["content"]
        return ""

    @property
    def joined(self) -> str:
        return "\n".join(m["content"] for m in self.messages)


def _compile_match(pattern: str | None) -> re.Pattern[str] | None:
    return re.compile(pattern, re.S) if pattern else None


class FakeLLM:
    """按脚本返回的 LLM。

    用法一 —— 按顺序：

        llm = FakeLLM(["第一次回答", "第二次回答"])

    用法二 —— 按内容匹配（顺序无关）：

        llm = FakeLLM([
            ("指标", '{"metric": "loan_balance"}'),
            ("澄清", '{"clarification_needed": true}'),
        ])

    用法三 —— 改写某一步：

        llm.push("补充回答")
    """

    def __init__(
        self,
        script: Sequence[str | tuple[str, str]] | None = None,
        *,
        default: str | None = None,
    ) -> None:
        self._steps: list[_Step] = []
        self._calls: list[Call] = []
        #: 设为非 None 时，脚本用尽后返回该默认值而不是抛错。
        #: 仅用于"调用次数不重要"的粗粒度测试。
        self._default = default
        for item in script or ():
            self.push(item)

    # -- 脚本管理 ----------------------------------------------------------
    def push(self, item: str | tuple[str, str]) -> FakeLLM:
        if isinstance(item, tuple):
            pattern, response = item
            self._steps.append(_Step(response=response, match=_compile_match(pattern)))
        else:
            self._steps.append(_Step(response=item))
        return self

    def reset(self) -> None:
        self._steps.clear()
        self._calls.clear()

    # -- 断言辅助 ----------------------------------------------------------
    @property
    def calls(self) -> list[Call]:
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def remaining(self) -> int:
        return len(self._steps)

    def assert_exhausted(self) -> None:
        if self._steps:
            pending = [s.response[:40] for s in self._steps]
            raise AssertionError(f"还有 {len(self._steps)} 条脚本未被消费: {pending}")

    def assert_called_once(self) -> None:
        if len(self._calls) != 1:
            raise AssertionError(f"期望调用 1 次，实际 {len(self._calls)} 次")

    # -- LLMClient 协议 ----------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        self._calls.append(
            Call(
                messages=canonical_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        step = self._take(self._calls[-1])
        if step is None:
            if self._default is not None:
                return Completion(text=self._default, model="fake")
            raise ScriptExhausted(
                f"FakeLLM 脚本用尽（已调用 {len(self._calls)} 次）。"
                f"最后一次 user 消息前 80 字：{self._calls[-1].last_user_content[:80]!r}"
            )
        return Completion(text=step.response, model="fake")

    def _take(self, call: Call) -> _Step | None:
        # 先找匹配当前调用内容的
        for index, step in enumerate(self._steps):
            if step.match is not None and step.match.search(call.joined):
                return self._steps.pop(index)
        # 再找无条件项
        for index, step in enumerate(self._steps):
            if step.match is None:
                return self._steps.pop(index)
        return None


def iter_script(items: Sequence[str | tuple[str, str]]) -> Iterator[_Step]:
    """把脚本项展开为 _Step，供需要预检的测试使用。"""
    for item in items:
        if isinstance(item, tuple):
            yield _Step(response=item[1], match=_compile_match(item[0]))
        else:
            yield _Step(response=item)
