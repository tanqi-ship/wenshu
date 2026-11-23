"""单轮业务意图抽取；不是可答性或权限门禁。"""

from app.engine.understanding.errors import ExtractionError
from app.engine.understanding.extractor import ExtractionResult, extract_intent

__all__ = ["ExtractionError", "ExtractionResult", "extract_intent"]
