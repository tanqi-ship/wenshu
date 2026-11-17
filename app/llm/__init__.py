"""模型访问层。

业务代码只应该 import 这里的 Protocol：

    from app.llm import Embedder, LLMClient, Message

需要构造实现时用 :mod:`app.llm.factory`。
"""

from app.llm.embedding import (
    DEFAULT_DIM,
    HashEmbedder,
    OpenAICompatEmbedder,
    cosine_similarity,
)
from app.llm.fake import FakeLLM, ScriptExhausted
from app.llm.protocol import (
    Completion,
    Embedder,
    LLMClient,
    LLMError,
    LLMProtocolError,
    LLMTransportError,
    Message,
    ReplayMiss,
    canonical_messages,
)
from app.llm.real import OpenAICompatLLM
from app.llm.replay import Cassette, ReplayLLM, message_key

__all__ = [
    "DEFAULT_DIM",
    "Cassette",
    "Completion",
    "Embedder",
    "FakeLLM",
    "HashEmbedder",
    "LLMClient",
    "LLMError",
    "LLMProtocolError",
    "LLMTransportError",
    "Message",
    "OpenAICompatEmbedder",
    "OpenAICompatLLM",
    "ReplayLLM",
    "ReplayMiss",
    "ScriptExhausted",
    "canonical_messages",
    "cosine_similarity",
    "message_key",
]
