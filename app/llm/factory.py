"""按配置装配模型访问实现。

**唯一**允许 ``import app.llm.real`` / ``app.llm.fake`` 的地方（除了测试）。
其他模块只 import Protocol，这样替换实现不需要改业务代码。
"""

from __future__ import annotations

from app.config import Settings
from app.llm.embedding import HashEmbedder, OpenAICompatEmbedder
from app.llm.fake import FakeLLM
from app.llm.protocol import Embedder, LLMClient
from app.llm.real import OpenAICompatLLM
from app.llm.replay import ReplayLLM, build_cassette_path

DEFAULT_CASSETTE_NAME = "_shared"


def build_llm(settings: Settings, *, cassette: str | None = None) -> LLMClient:
    """按 ``settings.llm_mode`` 构造客户端。

    *cassette* 只在 replay 模式下有意义：默认读 ``_shared.json``，
    评测/回归时传 case id 读取 ``<case_id>.json``。
    """
    mode = settings.llm_mode

    if mode == "fake":
        # 空脚本 + default 空串：单元测试应显式构造 FakeLLM，这里只兜底，
        # 让"没配好就把管道跑通"的场景不至于崩。
        return FakeLLM([], default="")

    if mode == "replay":
        name = cassette or DEFAULT_CASSETTE_NAME
        path = build_cassette_path(settings.llm_cassette_dir, name)
        return ReplayLLM(
            path,
            inner=None,
            strict=True,
        )

    return OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        default_timeout_s=settings.llm_timeout_s,
        thinking=settings.llm_thinking,
    )


def build_embedder(settings: Settings) -> Embedder:
    if settings.embed_mode == "fake":
        return HashEmbedder(dim=settings.embed_dim)
    return OpenAICompatEmbedder(
        base_url=settings.embed_base_url,
        api_key=settings.embed_api_key,
        model=settings.embed_model,
        dim=settings.embed_dim,
    )


def build_recorder(settings: Settings, *, cassette: str) -> ReplayLLM:
    """录制模式：调真实模型并把结果落进 cassette。

    仅供本地使用::

        WENSHU_LLM_MODE=real WENSHU_LLM_API_KEY=... \\
          python -m scripts.record_cassette --case AQ-001
    """
    inner = OpenAICompatLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        default_timeout_s=settings.llm_timeout_s,
        thinking=settings.llm_thinking,
    )
    path = build_cassette_path(settings.llm_cassette_dir, cassette)
    return ReplayLLM(path, inner=inner, strict=False)
