"""澄清层：无状态单轮准备 + 确定性槽位检查；保留旧预算接口。"""

from app.engine.clarify.preparation import Clarification, PreparationResult, prepare_question
from app.engine.clarify.slots import (
    DEFAULT_SLOTS,
    INTENT_SLOTS,
    ClarifyState,
    SlotCheck,
    SlotSpec,
    build_answer_note,
    check_slots,
)

__all__ = [
    "DEFAULT_SLOTS",
    "INTENT_SLOTS",
    "Clarification",
    "ClarifyState",
    "PreparationResult",
    "SlotCheck",
    "SlotSpec",
    "build_answer_note",
    "check_slots",
    "prepare_question",
]
