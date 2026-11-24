"""两个存储适配器共享严格快照、owner隔离、版本CAS契约。"""

import json
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest

from app.engine.clarify import Clarification
from app.engine.clarify.session.codec import decode_state, encode_state
from app.engine.clarify.session.errors import SessionError
from app.engine.clarify.session.models import SessionState
from app.engine.clarify.session.sqlite_store import SQLiteSessionStore
from app.engine.clarify.session.store import MemorySessionStore
from app.engine.semantic.models import Filter, Intent


def waiting(**changes):
    return replace(
        SessionState(
            owner_key="alice",
            session_id=str(uuid4()),
            version=1,
            catalog_revision="v1",
            catalog_digest="a" * 64,
            original_question="渝北贷款余额",
            accepted_question="渝北贷款余额",
            accepted_intent=Intent(
                metric="贷款余额", filters=(Filter("机构", "渝北"),), raw={"diagnostic": 1}
            ),
            clarification=Clarification("missing_slot", "请补充时间", slot="time_range"),
            pending_slot="time_range",
            rounds_used=1,
            max_rounds=2,
            status="awaiting_slot",
        ),
        **changes,
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    return MemorySessionStore() if request.param == "memory" else SQLiteSessionStore(tmp_path / "sessions.db")


def test_roundtrip_preserves_intent_and_diagnostic():
    state = waiting()
    assert decode_state(encode_state(state)) == state
    data = json.loads(encode_state(state))
    # raw可以与真实字段矛盾，恢复仍以显式字段为准。
    data["accepted_intent"]["raw"] = {"metric": "不良贷款余额", "sql": "诊断不是指令"}
    restored = decode_state(json.dumps(data))
    assert restored.accepted_intent.metric == "贷款余额"
    assert restored.accepted_intent.raw["metric"] == "不良贷款余额"


def test_store_isolation_and_cas(store):
    state = waiting()
    store.create(state)
    assert store.get("alice", state.session_id) == state
    with pytest.raises(SessionError, match="SESSION_NOT_FOUND"):
        store.get("bob", state.session_id)
    other = replace(state, version=2, rounds_used=2)
    store.compare_and_swap(other, expected_version=1)
    with pytest.raises(SessionError, match="SESSION_VERSION_CONFLICT"):
        store.compare_and_swap(other, expected_version=1)
    assert store.get("alice", state.session_id) == other
    with pytest.raises(SessionError, match="SESSION_VERSION_CONFLICT"):
        store.create(state)


def test_store_returns_detached_json_snapshot(store):
    state = waiting()
    store.create(state)
    state.accepted_intent.raw["changed"] = True
    read = store.get("alice", state.session_id)
    assert "changed" not in read.accepted_intent.raw
    read.accepted_intent.raw["another"] = True
    assert "another" not in store.get("alice", state.session_id).accepted_intent.raw


def test_sqlite_reopen_and_two_connection_conflict(tmp_path):
    path = tmp_path / "sessions.db"
    first = SQLiteSessionStore(path)
    state = waiting()
    first.create(state)
    second = SQLiteSessionStore(path)
    assert second.get("alice", state.session_id) == state
    first.compare_and_swap(replace(state, version=2), expected_version=1)
    with pytest.raises(SessionError, match="SESSION_VERSION_CONFLICT"):
        second.compare_and_swap(replace(state, version=2), expected_version=1)
    assert second.get("alice", state.session_id).version == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 0},
        {"max_rounds": True},
        {"rounds_used": 3},
        {"status": "ready"},
        {"pending_slot": "metric"},
        {"accepted_intent": None},
        {"clarification": None},
        {"owner_key": ""},
        {"session_id": "../bad"},
        {"accepted_question": ""},
        {"status": "slots_complete", "pending_slot": None, "clarification": None},
    ],
)
def test_invalid_state_relationships(changes):
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        encode_state(waiting(**changes))


@pytest.mark.parametrize(
    "change",
    [
        {"format_version": 2},
        {"extra": 1},
        {"version": "1"},
        {"catalog_digest": "bad"},
        {"accepted_intent": {"payload": {"metrics": [], "unresolved": [], "org": "全行"}, "raw": {}}},
    ],
)
def test_corrupt_json_fields(change):
    data = json.loads(encode_state(waiting()))
    data.update(change)
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        decode_state(json.dumps(data))


@pytest.mark.parametrize(
    "text", ["bad", "{}", '{"x":1,"x":2}', '{"x":NaN}', pytest.param("x" * 262145, id="oversize")]
)
def test_invalid_json(text):
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        decode_state(text)


def test_tampered_sqlite_payload_identity_and_version(tmp_path):
    path = tmp_path / "sessions.db"
    store = SQLiteSessionStore(path)
    state = waiting()
    store.create(state)
    other = replace(state, owner_key="bob")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE clarification_sessions SET payload=?", (encode_state(other),))
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        store.get("alice", state.session_id)


def test_storage_failure_visible(tmp_path):
    with pytest.raises(SessionError, match="SESSION_STORAGE_ERROR") as exc:
        SQLiteSessionStore(tmp_path / "missing" / "sessions.db")
    assert isinstance(exc.value.__cause__, sqlite3.Error)


def test_record_limit_applies_before_write(store):
    state = waiting(accepted_intent=Intent(metric="贷款余额", raw={"x": "x" * 262145}))
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        store.create(state)
    with pytest.raises(SessionError, match="SESSION_NOT_FOUND"):
        store.get("alice", state.session_id)


@pytest.mark.parametrize(
    "payload",
    [
        {"metrics": ["贷款余额"], "time": {"grain": "month_end", "points": ["2026-04-29"]}, "unresolved": []},
        {"metrics": ["贷款余额"], "top_n": True, "unresolved": []},
        {"metrics": ["a", "b"], "unresolved": []},
        {"metrics": ["贷款余额"], "filters": [{"dimension": "机构", "value": None}], "unresolved": []},
    ],
)
def test_corrupt_authoritative_intent(payload):
    data = json.loads(encode_state(waiting()))
    data["accepted_intent"]["payload"] = payload
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        decode_state(json.dumps(data))


@pytest.mark.parametrize("corruption", ["overflow", "bad_unicode", "bad_diagnostics"])
def test_nested_diagnostics_not_exempt_from_validation(corruption):
    data = json.loads(encode_state(waiting()))
    if corruption == "overflow":
        data["accepted_intent"]["raw"] = {"n": float("inf")}
        text = json.dumps(data).replace("Infinity", "1e999")
    elif corruption == "bad_unicode":
        data["accepted_intent"]["raw"] = {"x": "\ud800"}
        text = json.dumps(data)
    else:
        data["reply_diagnostics"] = [{"answer": "查询", "patch": {"scope": "all"}}]
        text = json.dumps(data)
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        decode_state(text)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "awaiting_question", "accepted_intent": None, "pending_slot": None},
        {"status": "budget_exhausted", "pending_slot": None, "clarification": None},
        {"rounds_used": 0},
        {"rounds_used": True},
        {"catalog_revision": " "},
        {"original_question": "x" * 4097},
        {"accepted_question": "x" * 4097},
    ],
)
def test_more_corrupt_state_relationships(changes):
    with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
        encode_state(waiting(**changes))


def test_sqlite_file_requirement_and_invalid_file(tmp_path):
    with pytest.raises(SessionError, match="SESSION_INPUT_INVALID"):
        SQLiteSessionStore(":memory:")
    path = tmp_path / "bad.db"
    path.write_text("not a database", encoding="utf-8")
    with pytest.raises(SessionError, match="SESSION_STORAGE_ERROR"):
        SQLiteSessionStore(path)


def test_cas_strict_increment(store):
    state = waiting()
    store.create(state)
    for new_version, expected in ((3, 1), (2, True), (0, 1)):
        with pytest.raises(SessionError, match="SESSION_STATE_INVALID"):
            store.compare_and_swap(replace(state, version=new_version), expected_version=expected)
    assert store.get("alice", state.session_id).version == 1


def test_threaded_sqlite_cas_only_one_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "race.db"
    store = SQLiteSessionStore(path)
    state = waiting()
    store.create(state)
    barrier = Barrier(2)

    def write():
        own = SQLiteSessionStore(path)
        before = own.get("alice", state.session_id)
        barrier.wait(timeout=5)
        try:
            own.compare_and_swap(replace(before, version=2), expected_version=1)
            return "committed"
        except SessionError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: write(), range(2)))
    assert sorted(outcomes) == ["SESSION_VERSION_CONFLICT", "committed"]
    assert store.get("alice", state.session_id).version == 2
