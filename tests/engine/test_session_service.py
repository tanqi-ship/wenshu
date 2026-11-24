"""跨请求合并、预算、身份/版本和失败原子性。"""

import asyncio
import json
from dataclasses import replace
from datetime import date

import pytest

from app.engine.clarify.session import (
    ClarificationService,
    MemorySessionStore,
    SessionError,
    SQLiteSessionStore,
)
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.understanding import ExtractionError
from app.llm.fake import FakeLLM
from app.llm.protocol import Completion, LLMProtocolError, LLMTransportError, ReplayMiss

CAT = semantic_catalog()
START = {
    "metrics": ["贷款余额"],
    "filters": [{"dimension": "机构", "value": "渝北"}],
    "dimensions": ["产品"],
    "source_mode": "report",
    "comparison": "yoy",
    "top_n": 2,
    "share": {"dimension": "产品"},
    "unresolved": [],
}
TIME = {"grain": "month_end", "points": ["2026-04-30"]}
PATCH = {"slot": "time_range", "status": "filled", "value": TIME, "unresolved": []}


def fake(*data):
    return FakeLLM([json.dumps(d, ensure_ascii=False) for d in data])


def service(llm, store=None, budget=2):
    return ClarificationService(store=store or MemorySessionStore(), llm=llm, max_rounds=budget)


async def start(s, question="渝北贷款余额", **kwargs):
    return await s.start(question, CAT, owner_key="alice", catalog_revision="v1", **kwargs)


async def reply(s, result, answer="2026年4月末", **kwargs):
    args = {"owner_key": "alice", "catalog_revision": "v1", "expected_version": result.version, **kwargs}
    return await s.reply(result.session_id, answer, CAT, **args)


async def test_only_time_replaced_and_protocol_isolated():
    llm = fake(START, PATCH)
    store = MemorySessionStore()
    s = service(llm, store)
    first = await start(s)
    assert (first.status, first.version, first.rounds_used) == ("awaiting_slot", 1, 1)
    assert first.intent is None
    old = store.get("alice", first.session_id).accepted_intent
    last = await reply(s, first)
    assert (last.status, last.version, last.rounds_used) == ("slots_complete", 2, 1)
    assert last.intent.time_range.points == (date(2026, 4, 30),)
    assert replace(last.intent, time_range=None) == old
    assert llm.call_count == 2
    assert '"protocol_version":"slot-reply-v1"' in llm.calls[1].last_user_content
    assert "intent-extraction-v1" not in llm.calls[1].last_user_content
    assert store.get("alice", first.session_id).reply_diagnostics[0]["answer"] == "2026年4月末"
    assert last.completion.text == json.dumps(PATCH, ensure_ascii=False)


async def test_metric_patch_preserves_time_and_filters_after_reopen(tmp_path):
    path = tmp_path / "sessions.db"
    llm = fake(
        {**START, "metrics": [], "time": TIME},
        {"slot": "metric", "status": "filled", "value": ["未知指标"], "unresolved": []},
    )
    first_service = service(llm, SQLiteSessionStore(path))
    first = await start(first_service)
    old = first_service.store.get("alice", first.session_id).accepted_intent
    second_service = service(llm, SQLiteSessionStore(path))
    last = await reply(second_service, first, "未知指标")
    assert last.intent.metric == "未知指标"
    assert replace(last.intent, metric=None) == old
    assert second_service.show(first.session_id, owner_key="alice").completion is None
    assert llm.call_count == 2


async def test_two_missing_slots_last_allowed_answer_completes():
    llm = fake(
        {"metrics": [], "unresolved": []},
        {"slot": "metric", "status": "filled", "value": ["贷款余额"], "unresolved": []},
        PATCH,
    )
    s = service(llm)
    one = await start(s)
    assert one.clarification.slot == "metric"
    two = await reply(s, one, "贷款余额")
    assert (two.status, two.rounds_used, two.clarification.slot) == ("awaiting_slot", 2, "time_range")
    three = await reply(s, two)
    assert (three.status, three.rounds_used) == ("slots_complete", 2)
    assert three.intent.metric == "贷款余额"
    assert llm.call_count == 3


async def test_unclear_then_budget_exhaustion_no_guess():
    unclear = {"slot": "time_range", "status": "unclear", "value": None, "unresolved": ["去年"]}
    llm = fake(START, unclear, unclear)
    s = service(llm)
    first = await start(s)
    second = await reply(s, first, "去年")
    assert (second.status, second.rounds_used) == ("awaiting_slot", 2)
    last = await reply(s, second, "去年")
    assert last.status == "budget_exhausted"
    assert last.intent is None and last.clarification is None
    assert s.store.get("alice", last.session_id).accepted_intent.time_range is None
    with pytest.raises(SessionError, match="SESSION_CLOSED"):
        await reply(s, last)
    assert llm.call_count == 3


@pytest.mark.parametrize(
    "payload,status",
    [
        (START, "budget_exhausted"),
        ({**START, "time": TIME}, "slots_complete"),
        ({"metrics": ["a", "b"], "unresolved": []}, "budget_exhausted"),
    ],
)
async def test_zero_budget(payload, status):
    s = service(fake(payload), budget=0)
    result = await start(s)
    assert (result.status, result.rounds_used) == (status, 0)
    assert result.clarification is None


@pytest.mark.parametrize("budget", [-1, True, "2", 2.0, None])
def test_budget_strict(budget):
    with pytest.raises(SessionError, match="SESSION_INPUT_INVALID"):
        service(FakeLLM([]), budget=budget)


@pytest.mark.parametrize(
    "initial",
    [
        {"metrics": ["贷款余额", "不良贷款余额"], "unresolved": []},
        {"metrics": ["贷款余额"], "unresolved": ["去年"]},
    ],
)
async def test_full_restatement_no_partial_merge(initial):
    llm = fake(initial, {"metrics": ["不良贷款余额"], "time": TIME, "unresolved": []})
    s = service(llm)
    first = await start(s, "去年渝北的余额")
    assert first.status == "awaiting_question"
    assert "完整问题" in first.clarification.question
    assert s.store.get("alice", first.session_id).accepted_intent is None
    last = await reply(s, first, "2026年4月末不良贷款余额")
    assert last.status == "slots_complete"
    assert not last.intent.filters
    assert last.intent.metric == "不良贷款余额"
    assert json.loads(llm.calls[1].last_user_content)["question"] == "2026年4月末不良贷款余额"
    assert "slot-reply-v1" not in llm.calls[1].last_user_content


async def test_restatement_can_transition_to_slot_without_resetting_budget():
    llm = fake({"metrics": ["a", "b"], "unresolved": []}, {"metrics": ["贷款余额"], "unresolved": []}, PATCH)
    s = service(llm)
    one = await start(s)
    two = await reply(s, one, "贷款余额")
    assert (two.status, two.rounds_used) == ("awaiting_slot", 2)
    assert s.store.get("alice", two.session_id).accepted_question == "贷款余额"
    last = await reply(s, two)
    assert last.status == "slots_complete"
    assert not last.intent.filters


@pytest.mark.parametrize(
    "patch",
    [
        {"slot": "time_range", "status": "restart_required", "value": None, "unresolved": ["改查江北"]},
        {"slot": "metric", "status": "filled", "value": ["贷款余额", "不良贷款余额"], "unresolved": []},
    ],
)
async def test_changed_conditions_or_multiple_metrics_require_restart(patch):
    initial = START if patch["slot"] == "time_range" else {**START, "metrics": [], "time": TIME}
    llm = fake(initial, patch)
    s = service(llm)
    first = await start(s)
    old = s.store.get("alice", first.session_id).accepted_intent
    last = await reply(s, first, "改查江北，贷款余额和不良贷款余额")
    assert last.status == "restart_required" and last.intent is None
    assert s.store.get("alice", first.session_id).accepted_intent == old
    assert llm.call_count == 2


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"owner_key": "bob"}, "NOT_FOUND"),
        ({"expected_version": 2}, "VERSION_CONFLICT"),
        ({"expected_version": True}, "INPUT_INVALID"),
        ({"expected_version": "1"}, "INPUT_INVALID"),
        ({"owner_key": ""}, "INPUT_INVALID"),
    ],
)
async def test_invalid_access_or_version_zero_call(kwargs, code):
    llm = fake(START)
    s = service(llm)
    first = await start(s)
    with pytest.raises(SessionError, match="SESSION_" + code):
        await reply(s, first, **kwargs)
    assert llm.call_count == 1
    assert s.show(first.session_id, owner_key="alice").version == 1


async def test_unknown_session_zero_call():
    from uuid import uuid4

    llm = FakeLLM([])
    s = service(llm)
    with pytest.raises(SessionError, match="SESSION_NOT_FOUND"):
        await s.reply(str(uuid4()), "日期", CAT, owner_key="alice", catalog_revision="v1", expected_version=1)
    assert llm.call_count == 0


@pytest.mark.parametrize("answer", ["", None, True, pytest.param("x" * 4097, id="oversize"), "\ud800"])
async def test_invalid_answer_does_not_advance(answer):
    llm = fake(START)
    s = service(llm)
    first = await start(s)
    with pytest.raises(SessionError, match="SESSION_INPUT_INVALID"):
        await reply(s, first, answer)
    assert s.show(first.session_id, owner_key="alice").version == 1
    assert llm.call_count == 1


@pytest.mark.parametrize("change", ["revision", "business_digest"])
async def test_catalog_change_restarts_zero_model_call(change):
    llm = fake(START)
    s = service(llm)
    first = await start(s)
    catalog = CAT
    rev = "v2" if change == "revision" else "v1"
    if change == "business_digest":
        metrics = dict(CAT.metrics)
        metrics["loan_balance"] = replace(metrics["loan_balance"], unit="万元")
        catalog = replace(CAT, metrics=metrics)
    last = await s.reply(
        first.session_id, "2026年4月末", catalog, owner_key="alice", catalog_revision=rev, expected_version=1
    )
    assert (last.status, last.version) == ("restart_required", 2)
    assert last.completion is None
    assert llm.call_count == 1


@pytest.mark.parametrize(
    "response,code",
    [
        ("bad", "SESSION_REPLY_JSON_INVALID"),
        ('{"slot":"metric","status":"filled","value":["x"],"unresolved":[]}', "SESSION_REPLY_SCHEMA_INVALID"),
        (
            '{"slot":"time_range","status":"filled","value":{"grain":"day"},"unresolved":[]}',
            "SESSION_REPLY_TIME_INVALID",
        ),
    ],
)
async def test_bad_response_no_persist(response, code):
    llm = FakeLLM([json.dumps(START), response])
    s = service(llm)
    first = await start(s)
    before = s.store.get("alice", first.session_id)
    with pytest.raises(SessionError, match=code):
        await reply(s, first)
    assert s.store.get("alice", first.session_id) == before
    assert llm.call_count == 2


@pytest.mark.parametrize("error_type", [LLMTransportError, LLMProtocolError, ReplayMiss])
async def test_model_failure_identity_and_retry_is_caller_owned(error_type):
    error = error_type("原始异常")

    class LLM:
        calls = 0

        async def complete(self, messages, **kwargs):
            self.calls += 1
            assert kwargs == {"temperature": 0, "max_tokens": 2048, "timeout_s": 30}
            if self.calls == 1:
                return Completion(json.dumps(START))
            raise error

    llm = LLM()
    s = service(llm)
    first = await start(s)
    with pytest.raises(error_type) as exc:
        await reply(s, first)
    assert exc.value is error
    assert s.show(first.session_id, owner_key="alice").version == 1
    assert llm.calls == 2


async def test_concurrent_replies_only_one_commits_no_automatic_retry():
    gate = asyncio.Event()

    class LLM:
        calls = 0
        replies = 0

        async def complete(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return Completion(json.dumps(START))
            self.replies += 1
            if self.replies == 2:
                gate.set()
            await gate.wait()
            return Completion(json.dumps(PATCH))

    llm = LLM()
    s = service(llm)
    first = await start(s)
    outcomes = await asyncio.gather(reply(s, first), reply(s, first), return_exceptions=True)
    assert len([x for x in outcomes if not isinstance(x, Exception)]) == 1
    errors = [x for x in outcomes if isinstance(x, SessionError)]
    assert len(errors) == 1 and errors[0].code == "SESSION_VERSION_CONFLICT"
    assert s.show(first.session_id, owner_key="alice").version == 2
    assert llm.calls == 3


async def test_prompt_minimal_and_explicit_confirmed_fields():
    llm = fake(START, PATCH)
    s = service(llm)
    first = await start(s)
    await reply(s, first)
    data = json.loads(llm.calls[1].last_user_content)
    assert set(data) == {
        "protocol_version",
        "question",
        "clarification",
        "pending_slot",
        "confirmed",
        "catalog",
        "answer",
    }
    assert data["confirmed"]["filters"] == [{"dimension": "机构", "operator": "eq", "value": "渝北"}]
    content = llm.calls[1].joined
    for secret in (
        "owner_key",
        "alice",
        first.session_id,
        "catalog_revision",
        "code_map",
        "raw",
        "qa_loan_acc_detail",
        "org_no",
        "B001",
    ):
        if secret == "raw":
            assert "raw" not in data["confirmed"]
        else:
            assert secret not in content


async def test_sqlite_write_failure_does_not_claim_completion(tmp_path):
    import sqlite3

    path = tmp_path / "sessions.db"
    llm = fake(START, PATCH)
    s = service(llm, SQLiteSessionStore(path))
    first = await start(s)
    before = s.store.get("alice", first.session_id)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER block_update BEFORE UPDATE ON clarification_sessions BEGIN SELECT RAISE(ABORT, 'test write failure'); END"
        )
    with pytest.raises(SessionError, match="SESSION_STORAGE_ERROR"):
        await reply(s, first)
    assert s.store.get("alice", first.session_id) == before
    assert llm.call_count == 2


async def test_create_failure_does_not_return_session_success():
    class FailingStore(MemorySessionStore):
        def create(self, state):
            raise SessionError("SESSION_STORAGE_ERROR", "test create failure")

    llm = fake(START)
    s = service(llm, FailingStore())
    with pytest.raises(SessionError, match="SESSION_STORAGE_ERROR"):
        await start(s)
    assert llm.call_count == 1


async def test_restatement_failure_keeps_original_session_and_budget():
    llm = FakeLLM(['{"metrics":["a","b"],"unresolved":[]}', "bad json"])
    s = service(llm)
    first = await start(s)
    before = s.store.get("alice", first.session_id)
    with pytest.raises(ExtractionError, match="EXTRACTION_JSON_INVALID"):
        await reply(s, first, "新问题")
    assert s.store.get("alice", first.session_id) == before
    assert llm.call_count == 2


async def test_repeated_restatement_exhausts_without_partial_intent():
    llm = fake(
        {"metrics": ["a", "b"], "unresolved": []},
        {"metrics": ["a", "b"], "unresolved": []},
        {"metrics": ["a", "b"], "unresolved": []},
    )
    s = service(llm)
    one = await start(s)
    two = await reply(s, one, "a和b")
    assert two.rounds_used == 2 and two.status == "awaiting_question"
    last = await reply(s, two, "a和b")
    assert last.status == "budget_exhausted" and last.intent is None
    assert s.store.get("alice", last.session_id).accepted_intent is None


async def test_bad_current_catalog_zero_call_and_no_write():
    llm = fake(START)
    s = service(llm)
    first = await start(s)
    with pytest.raises(ExtractionError, match="EXTRACTION_CONTEXT_INVALID"):
        await s.reply(
            first.session_id,
            "2026年4月末",
            None,
            owner_key="alice",
            catalog_revision="v1",
            expected_version=1,
        )
    assert s.show(first.session_id, owner_key="alice").version == 1
    assert llm.call_count == 1


@pytest.mark.parametrize("owner,revision", [("", "v1"), (None, "v1"), ("alice", ""), ("alice", 1)])
async def test_start_bad_metadata_zero_call(owner, revision):
    llm = FakeLLM([])
    s = service(llm)
    with pytest.raises(SessionError, match="SESSION_INPUT_INVALID"):
        await s.start("问题", CAT, owner_key=owner, catalog_revision=revision)
    assert llm.call_count == 0


async def test_complete_first_turn_and_closed_zero_extra_calls():
    llm = fake({**START, "time": TIME})
    s = service(llm)
    first = await start(s)
    assert first.status == "slots_complete" and first.rounds_used == 0
    with pytest.raises(SessionError, match="SESSION_CLOSED"):
        await reply(s, first)
    assert llm.call_count == 1


def test_import_direction_no_execution_or_hidden_model():
    import ast
    from pathlib import Path

    forbidden = (
        "app.engine.compiler",
        "app.engine.validation",
        "app.engine.coverage",
        "app.llm.factory",
        "app.llm.real",
        "app.config",
        "os",
        "httpx",
        "pickle",
    )
    for path in Path("app/engine/clarify/session").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(m and m.startswith(forbidden) for m in modules), path
            if isinstance(node, ast.Name):
                assert node.id not in {"ClarifyState", "build_answer_note", "eval", "exec"}, path
