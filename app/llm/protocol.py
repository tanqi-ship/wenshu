"""模型访问边界。

这是整个项目**第一天就必须定下来**的架构决定。

为什么：NL2SQL 系统的绝大多数逻辑都绕不开大模型，但 CI 里绝对不能调真实模型
（不可复现、要联网、会超时、要花钱）。如果一开始把 ``httpx.post(...)`` 散落在
各处，后面就再也加不回来了。

所以所有模型访问必须经过这里的 Protocol，实现由 ``WENSHU_LLM_MODE`` 决定：

    fake    -> FakeLLM      单元测试：按脚本返回，零网络
    replay  -> ReplayLLM    CI / 回归：回放 cassette，零网络
    real    -> OpenAICompatLLM  本地 / 生产

约定（违反这些约定的实现视为 bug）：

1. ``complete`` 是 async，因为真实调用是 IO。
2. 实现不得自行重试 —— 重试策略属于引擎层，便于测试与统一预算。
3. 实现不得吞异常。网络/解析错误一律抛 ``LLMError`` 的子类。
4. 回放实现找不到记录时必须**抛错**，不得回落到网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence, runtime_checkable

Role = Literal["system", "user", "assistant"]


class LLMError(Exception):
    """模型访问失败的基类。"""


class LLMTransportError(LLMError):
    """网络、超时、非 2xx 响应。"""


class LLMProtocolError(LLMError):
    """响应格式不符合预期。"""


class ReplayMiss(LLMError):
    """回放模式找不到对应记录。

    这**不是**可以静默降级的错误。CI 里出现它意味着：
    cassette 过期了，或者代码改了却没重新录制 —— 两种情况都必须让人看到。
    """


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMClient(Protocol):
    """文本补全。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        ...


@runtime_checkable
class Embedder(Protocol):
    """文本向量化。

    ``dim`` 必须是稳定值：向量要落库，维度变化等于索引全部作废。
    """

    @property
    def dim(self) -> int:
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回与 ``texts`` 等长的向量列表。"""
        ...


def canonical_messages(messages: Sequence[Message]) -> list[dict[str, str]]:
    """把消息序列标准化成可哈希的 JSON 结构。

    FakeLLM / ReplayLLM / cassette 录制都依赖这个函数，保证三处口径一致。
    """
    return [m.as_dict() for m in messages]
