"""单轮状态机与原子提交；一次补答最多一次模型调用。"""

from dataclasses import replace
from hashlib import sha256
from uuid import UUID, uuid4

from app.engine.clarify.preparation import _prepare_extraction, prepare_question
from app.engine.clarify.session.errors import SessionError, fail
from app.engine.clarify.session.models import SessionState, result_from_state
from app.engine.clarify.session.reply import build_reply_messages, parse_reply
from app.engine.clarify.session.store import SessionStore
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import intent_from_mapping
from app.engine.understanding import ExtractionResult
from app.engine.understanding.context import build_messages, business_context, stable_json
from app.llm.protocol import LLMClient


def _input_text(value, path, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        fail("INPUT_INVALID", "必须为非空且未超限的字符串。", path)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise SessionError("SESSION_INPUT_INVALID", "输入不是UTF-8文本。", path=path) from exc


def _identity(session_id, owner_key):
    _input_text(owner_key, "owner_key")
    _input_text(session_id, "session_id", 36)
    try:
        if str(UUID(session_id)) != session_id:
            raise ValueError("非规范UUID")
    except ValueError as exc:
        raise SessionError("SESSION_INPUT_INVALID", "会话ID必须为规范UUID。", path="session_id") from exc


def _digest(catalog):
    return sha256(stable_json(business_context(catalog)).encode("utf-8")).hexdigest()


class ClarificationService:
    def __init__(self, *, store: SessionStore, llm: LLMClient, max_rounds: int = 2):
        if type(max_rounds) is not int or max_rounds < 0:
            fail("INPUT_INVALID", "max_rounds必须为非负整数。", "max_rounds")
        self.store = store
        self.llm = llm
        self.max_rounds = max_rounds

    async def start(self, question: str, catalog: SemanticCatalog, *, owner_key: str, catalog_revision: str):
        _input_text(owner_key, "owner_key")
        _input_text(catalog_revision, "catalog_revision")
        build_messages(question, catalog)  # 使用原输入/目录边界；没有隐藏模型调用。
        digest = _digest(catalog)
        prepared = await prepare_question(question, catalog, llm=self.llm)
        base = SessionState(
            owner_key=owner_key,
            session_id=str(uuid4()),
            version=1,
            catalog_revision=catalog_revision,
            catalog_digest=digest,
            original_question=question,
            accepted_question=question,
            accepted_intent=None,
            clarification=None,
            pending_slot=None,
            rounds_used=0,
            max_rounds=self.max_rounds,
            status="slots_complete",
        )
        state = self._from_prepared(base, prepared)
        self.store.create(state)
        completion = prepared.extraction.completion if prepared.extraction else None
        return result_from_state(state, completion)

    def show(self, session_id: str, *, owner_key: str):
        _identity(session_id, owner_key)
        return result_from_state(self.store.get(owner_key, session_id))

    def _from_prepared(self, state, prepared):
        intent = prepared.extraction.intent if prepared.extraction else None
        state = replace(state, accepted_intent=intent)
        if prepared.status == "slots_complete":
            return replace(
                state,
                status="slots_complete",
                clarification=None,
                pending_slot=None,
                note="必需槽位齐全；仍需下游检查，不代表可答或授权。",
            )
        return self._ask(state, prepared.clarification)

    def _ask(self, state, clarification):
        assert clarification is not None
        if state.rounds_used >= state.max_rounds:
            return replace(
                state,
                status="budget_exhausted",
                clarification=None,
                pending_slot=None,
                note="追问预算已用尽，必需信息仍未明确；停止本会话，请重新提交完整问题。",
            )
        if state.accepted_intent is None and "完整问题" not in clarification.question:
            # 无安全部分结果可沿用；不能暗示只答片段便会自动合并。
            clarification = replace(clarification, question=clarification.question + " 请重新提交完整问题。")
        return replace(
            state,
            status="awaiting_slot" if state.accepted_intent is not None else "awaiting_question",
            clarification=clarification,
            pending_slot=clarification.slot if state.accepted_intent is not None else None,
            rounds_used=state.rounds_used + 1,
            note="",
        )

    async def reply(
        self,
        session_id: str,
        answer: str,
        catalog: SemanticCatalog,
        *,
        owner_key: str,
        catalog_revision: str,
        expected_version: int,
    ):
        _identity(session_id, owner_key)
        _input_text(answer, "answer", 4096)
        _input_text(catalog_revision, "catalog_revision")
        if type(expected_version) is not int or expected_version < 1:
            fail("INPUT_INVALID", "expected_version必须为正整数。", "expected_version")
        old = self.store.get(owner_key, session_id)
        if old.version != expected_version:
            fail("VERSION_CONFLICT", "会话版本已变化。")
        if old.status not in ("awaiting_slot", "awaiting_question"):
            fail("CLOSED", "本会话已终止，请重新发起完整问题。")
        digest = _digest(catalog)  # 目录非法原CONTEXT_INVALID；状态保持。
        state = replace(old, version=old.version + 1)
        if old.catalog_revision != catalog_revision or old.catalog_digest != digest:
            state = replace(
                state,
                status="restart_required",
                clarification=None,
                pending_slot=None,
                note="业务目录已变化，请重新发起完整问题。",
            )
            self.store.compare_and_swap(state, expected_version=expected_version)
            return result_from_state(state)
        if old.status == "awaiting_question":
            prepared = await prepare_question(answer, catalog, llm=self.llm)
            state = self._from_prepared(replace(state, accepted_question=answer), prepared)
            completion = prepared.extraction.completion if prepared.extraction else None
        else:
            messages = build_reply_messages(old, answer, catalog)
            completion = await self.llm.complete(messages, temperature=0, max_tokens=2048, timeout_s=30)
            patch = parse_reply(completion.text, slot=old.pending_slot, answer=answer)
            # 仅记当前受限补答诊断，不追加Completion/权限/整段聊天历史。
            diagnostics = (
                *old.reply_diagnostics,
                {
                    "answer": answer,
                    "patch": {
                        "slot": old.pending_slot,
                        "status": patch.status,
                        "value": patch.value,
                        "unresolved": list(patch.fragments),
                    },
                },
            )
            state = replace(state, reply_diagnostics=diagnostics)
            if patch.status == "restart_required":
                state = replace(
                    state,
                    status="restart_required",
                    clarification=None,
                    pending_slot=None,
                    note="补答涉及多指标或条件变更，不能安全合并；请重新发起完整问题。",
                )
            elif patch.status == "unclear":
                state = self._ask(state, old.clarification)
            else:
                intent = old.accepted_intent
                assert intent is not None
                if old.pending_slot == "metric" and intent.metric is None:
                    intent = replace(intent, metric=patch.value[0])
                elif old.pending_slot == "time_range" and intent.time_range is None:
                    # 时间已经独立严格校验；这里仅复用领域对象构造，不依赖宽松加载器作验证。
                    time = intent_from_mapping({"time": patch.value}).time_range
                    intent = replace(intent, time_range=time)
                else:
                    fail("STATE_INVALID", "不能覆盖已经确认的槽位。")
                prepared = _prepare_extraction(ExtractionResult(intent, completion), catalog)
                state = self._from_prepared(state, prepared)
        # 不跨模型IO持有事务；CAS冲突可见，绝不自动调用模型第二次。
        self.store.compare_and_swap(state, expected_version=expected_version)
        return result_from_state(state, completion)
