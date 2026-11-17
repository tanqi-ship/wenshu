"""向量化实现。

开发和 CI 用 ``HashEmbedder``：确定性、零网络、跨进程稳定。
生产换 ``OpenAICompatEmbedder``。

⚠️ ``HashEmbedder`` 的**语义质量为零**。它只保证：

* 相同文本 -> 相同向量（跨进程、跨机器）
* 不同文本 -> 不同向量
* 向量已 L2 归一化，所以余弦相似度 = 点积

它的用途是让**管道**可测，不是让**检索**可用。
所以任何"检索召回率"的评测都必须在真实 embedder 上跑，不能用它。
"""

from __future__ import annotations

import hashlib
import math
import random

import httpx

from app.llm.protocol import LLMTransportError

DEFAULT_DIM = 256


class HashEmbedder:
    """基于 sha256 的确定性伪向量。"""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim 必须为正数")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        # random.Random 的 Mersenne Twister 序列在 CPython 各版本间稳定，
        # 所以同一文本在任何机器上得到同一向量。
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(self._dim)]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAICompatEmbedder:
    """OpenAI ``/embeddings`` 兼容端点。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str,
        dim: int = 0,
        timeout_s: float = 60.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url 不能为空")
        if not model:
            raise ValueError("model 不能为空")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._timeout_s = timeout_s

    @property
    def dim(self) -> int:
        # 首次调用前不知道维度时返回配置值；落库前必须确定下来。
        return self._dim

    def embed(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        attempts = 3
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                with httpx.Client(timeout=self._timeout_s) as client:
                    response = client.post(
                        f"{self._base_url}/embeddings",
                        json={"model": self._model, "input": list(texts)},
                        headers=self._headers(),
                    )
                if response.status_code >= 400:
                    raise LLMTransportError(
                        f"embedding 返回 {response.status_code}：{response.text[:300]}"
                    )
                body = response.json()
                vectors = [item["embedding"] for item in body["data"]]
                if vectors and not self._dim:
                    self._dim = len(vectors[0])
                return vectors
            except (LLMTransportError, KeyError, ValueError, TypeError) as exc:
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
        raise LLMTransportError(f"embedding 调用失败：{last_error}") from last_error

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """两个向量的余弦相似度。

    ``HashEmbedder`` 输出的向量已归一化，所以这里能省一次开方；
    但为了对任意输入都正确，仍然做完整计算。
    """
    if len(left) != len(right):
        raise ValueError(f"维度不一致：{len(left)} vs {len(right)}")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)
