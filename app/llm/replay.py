"""基于 cassette 的录制 / 回放。

一条铁律：**回放找不到记录时抛错，绝不联网。**
否则 CI 会在某天悄悄开始调用真实模型：慢、花钱、且结果不可复现 ——
而流水线还是绿的。

cassette 格式（JSON）::

    {
      "version": 1,
      "model": "deepseek-chat",
      "entries": {
        "<16 hex>": {
          "text": "...模型原始输出...",
          "model": "deepseek-chat",
          "prompt_tokens": 412,
          "completion_tokens": 96
        }
      }
    }

键的取法：消息序列的 sha256 前 16 位。**不包含 temperature / max_tokens。**

为什么只按消息取键 —— 两个好处：

1. cassette 更小、更好审阅：人读 cassette 时看到的就是"问了什么 → 答了什么"。
2. 它会把一个引擎 bug 变成可见的测试信号：
   如果某个重试调用**带着和上次完全相同的消息**再问一遍，回放会返回同样的
   回答，于是重试永远不可能修好任何东西。这种情况应该由引擎保证不发生
   （重试必须携带新信息，例如上一版 SQL + 错误原因），而不是靠 cassette 兜。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.llm.protocol import (
    Completion,
    LLMClient,
    Message,
    ReplayMiss,
    canonical_messages,
)

CASSETTE_VERSION = 1


def message_key(messages: Sequence[Message]) -> str:
    """消息序列 -> cassette 键。"""
    payload = json.dumps(canonical_messages(messages), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class Cassette:
    path: Path
    entries: dict[str, dict[str, Any]]
    model: str = ""

    @classmethod
    def load(cls, path: Path) -> Cassette:
        if not path.exists():
            return cls(path=path, entries={})
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if version != CASSETTE_VERSION:
            raise ReplayMiss(
                f"cassette 版本不匹配: {path} 是 v{version}，期望 v{CASSETTE_VERSION}。请重新录制。"
            )
        return cls(path=path, entries=dict(raw.get("entries") or {}), model=raw.get("model", ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CASSETTE_VERSION,
            "model": self.model,
            "entries": dict(sorted(self.entries.items())),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self.entries)


class ReplayLLM:
    """读 cassette 返回记录；可选地把未命中的调用转发给真实客户端并落盘。

    *``inner=None``* —— 纯回放（CI）。未命中一律抛错。
    *``inner=<real client>``* —— 录制模式。

    *``strict``* 决定录制模式下未命中怎么办：

    * ``True``（默认）—— 抛错。用于"这次运行必须只回放"的场景，
      例如 CI 里的回归门禁：任何一次新调用都说明 cassette 过期了，
      而这必须让人看到，不能被自动录制悄悄掩盖。
    * ``False`` —— 调 *inner* 并把结果落盘。用于本地首次录制。
    """

    def __init__(
        self,
        cassette_path: str | Path,
        *,
        inner: LLMClient | None = None,
        strict: bool = True,
        autosave: bool = True,
    ) -> None:
        self._cassette = Cassette.load(Path(cassette_path))
        self._inner = inner
        self._strict = strict
        self._autosave = autosave
        self._dirty = False
        self._hits = 0
        self._misses = 0

    # -- 统计 --------------------------------------------------------------
    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def recording(self) -> bool:
        return self._inner is not None

    def flush(self) -> None:
        if self._dirty and self._autosave:
            self._cassette.save()
            self._dirty = False

    # -- LLMClient 协议 ----------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        key = message_key(messages)
        recorded = self._cassette.entries.get(key)
        if recorded is not None:
            self._hits += 1
            return Completion(
                text=recorded["text"],
                model=recorded.get("model", self._cassette.model),
                prompt_tokens=int(recorded.get("prompt_tokens") or 0),
                completion_tokens=int(recorded.get("completion_tokens") or 0),
                meta={"replayed": True},
            )

        self._misses += 1
        # 纯回放：没有 inner 可回落，未命中就是错误。
        # 录制模式 + strict：也不许自动录制，否则 CI 会悄悄掩盖 cassette 过期。
        if self._inner is None or self._strict:
            raise ReplayMiss(self._miss_message(messages, key))

        completion = await self._inner.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        self._cassette.entries[key] = {
            "text": completion.text,
            "model": completion.model,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
        }
        if not self._cassette.model and completion.model:
            self._cassette.model = completion.model
        self._dirty = True
        return completion

    def _miss_message(self, messages: Sequence[Message], key: str) -> str:
        last_user = ""
        for message in reversed(messages):
            if message.role == "user":
                last_user = message.content
                break
        preview = last_user[:200].replace("\n", "\\n")
        return (
            f"cassette 未命中：{self._cassette.path} key={key}\n"
            f"cassette 现有 {len(self._cassette)} 条记录。\n"
            f"最后一次 user 消息（前 200 字）：{preview!r}\n"
            "提示：如果引擎的 prompt 有意改动了，需要重新录制 cassette。"
        )


class RecordingLLM(ReplayLLM):
    """``ReplayLLM(inner=...)`` 的具名别名，让调用处意图更清楚。"""


def build_cassette_path(root: str | Path, case_id: str) -> Path:
    """约定的 cassette 路径：``<root>/<case_id>.json``。"""
    return Path(root) / f"{case_id}.json"
