"""存储协议与线程内原子CAS内存实现；用编码快照隔离可变诊断数据。"""

from threading import Lock
from typing import Protocol

from app.engine.clarify.session.codec import decode_state, encode_state
from app.engine.clarify.session.errors import fail
from app.engine.clarify.session.models import SessionState


class SessionStore(Protocol):
    def create(self, state: SessionState) -> None: ...
    def get(self, owner_key: str, session_id: str) -> SessionState: ...
    def compare_and_swap(self, state: SessionState, *, expected_version: int) -> None: ...


def check_write_version(state, expected_version=None):
    if expected_version is None:
        if state.version != 1:
            fail("STATE_INVALID", "新会话版本必须为1。")
    elif type(expected_version) is not int or expected_version < 1 or state.version != expected_version + 1:
        fail("STATE_INVALID", "CAS写入必须严格递增一个版本。")


class MemorySessionStore:
    def __init__(self):
        self._entries = {}
        self._lock = Lock()

    def create(self, state):
        check_write_version(state)
        payload = encode_state(state)
        key = (state.owner_key, state.session_id)
        with self._lock:
            if key in self._entries:
                fail("VERSION_CONFLICT", "会话已存在。")
            self._entries[key] = payload

    def get(self, owner_key, session_id):
        with self._lock:
            payload = self._entries.get((owner_key, session_id))
        if payload is None:
            fail("NOT_FOUND", "会话不存在。")
        return decode_state(payload)

    def compare_and_swap(self, state, *, expected_version):
        check_write_version(state, expected_version)
        payload = encode_state(state)
        key = (state.owner_key, state.session_id)
        with self._lock:
            original = self._entries.get(key)
            if original is None or decode_state(original).version != expected_version:
                fail("VERSION_CONFLICT", "会话版本已变化。")
            self._entries[key] = payload
