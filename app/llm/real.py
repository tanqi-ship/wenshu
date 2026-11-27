"""OpenAI 兼容接口的真实客户端。

刻意保持薄：只做 HTTP + 错误归一化，不做重试、不做 JSON 修复。
重试与解析修复属于引擎层，那样才能被测试和预算约束。
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.llm.protocol import (
    Completion,
    LLMTransportError,
    Message,
    canonical_messages,
)


class OpenAICompatLLM:
    """任何 OpenAI ``/chat/completions`` 兼容端点。

    覆盖 DeepSeek、Qwen、通义、vLLM、Xinference、千帆 OpenAI 兼容模式等。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str,
        default_timeout_s: float = 120.0,
        extra_headers: dict[str, str] | None = None,
        thinking: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url 不能为空")
        if not model:
            raise ValueError("model 不能为空")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._default_timeout_s = default_timeout_s
        self._extra_headers = dict(extra_headers or {})
        self._thinking = thinking
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_s: float = 120.0,
    ) -> Completion:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": self._model,
            "messages": canonical_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self._thinking:
            payload["thinking"] = {"type": self._thinking}

        url = f"{self._base_url}/chat/completions"
        effective_timeout = timeout_s or self._default_timeout_s

        try:
            async with httpx.AsyncClient(timeout=effective_timeout, transport=self._transport) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMTransportError(f"模型调用超时（{effective_timeout}s）：{url}") from exc
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"模型调用网络错误：{type(exc).__name__}: {exc}") from exc

        if response.status_code >= 400:
            raise LLMTransportError(f"模型调用返回 {response.status_code}：{response.text[:400]}")

        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMTransportError(f"模型响应结构不符合预期：{response.text[:400]}") from exc

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            model=str(body.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            meta={"finish_reason": choice.get("finish_reason", "")},
        )
