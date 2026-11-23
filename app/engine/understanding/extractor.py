"""一次注入模型调用 → 严格边界；不编译、不执行、不判断可答。"""

from dataclasses import dataclass

from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Intent
from app.engine.understanding.context import build_messages
from app.engine.understanding.schema import parse_intent
from app.llm.protocol import Completion, LLMClient


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    intent: Intent
    completion: Completion


async def extract_intent(question: str, catalog: SemanticCatalog, *, llm: LLMClient) -> ExtractionResult:
    messages = build_messages(question, catalog)
    completion = await llm.complete(messages, temperature=0, max_tokens=2048, timeout_s=30)
    intent = parse_intent(completion.text, question=question)
    return ExtractionResult(intent=intent, completion=completion)
