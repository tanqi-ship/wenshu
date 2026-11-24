"""冻结的会话快照和单次响应；slots_complete不等于可答。"""

from dataclasses import dataclass
from typing import Literal

from app.engine.clarify.preparation import Clarification
from app.engine.semantic.models import Intent
from app.llm.protocol import Completion

Status = Literal[
    "awaiting_slot", "awaiting_question", "slots_complete", "budget_exhausted", "restart_required"
]


@dataclass(frozen=True, slots=True)
class SessionState:
    owner_key: str
    session_id: str
    version: int
    catalog_revision: str
    catalog_digest: str
    original_question: str
    accepted_question: str
    accepted_intent: Intent | None
    clarification: Clarification | None
    pending_slot: str | None
    rounds_used: int
    max_rounds: int
    status: Status
    note: str = ""
    reply_diagnostics: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionResult:
    session_id: str
    version: int
    status: Status
    rounds_used: int
    max_rounds: int
    clarification: Clarification | None
    note: str
    intent: Intent | None
    completion: Completion | None


def result_from_state(state: SessionState, completion: Completion | None = None) -> SessionResult:
    return SessionResult(
        session_id=state.session_id,
        version=state.version,
        status=state.status,
        rounds_used=state.rounds_used,
        max_rounds=state.max_rounds,
        clarification=state.clarification,
        note=state.note,
        intent=state.accepted_intent if state.status == "slots_complete" else None,
        completion=completion,
    )
