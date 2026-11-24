"""本地应用会话存储，固定SQL/绑定参数；不执行业务查询。"""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.engine.clarify.session.codec import decode_state, encode_state
from app.engine.clarify.session.errors import SessionError, fail
from app.engine.clarify.session.store import check_write_version


class SQLiteSessionStore:
    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            fail("INPUT_INVALID", "SQLite适配器需要文件路径；内存测试请使用MemorySessionStore。")
        self.path = Path(path)
        with self._connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS clarification_sessions (owner_key TEXT NOT NULL, session_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner_key,session_id))"
            )

    @contextmanager
    def _connection(self):
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                yield db
        except sqlite3.Error as exc:
            raise SessionError("SESSION_STORAGE_ERROR", "SQLite会话读写失败。") from exc

    def create(self, state):
        check_write_version(state)
        payload = encode_state(state)
        with self._connection() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO clarification_sessions (owner_key,session_id,version,payload) VALUES (?,?,?,?)",
                (state.owner_key, state.session_id, state.version, payload),
            )
            if cursor.rowcount != 1:
                fail("VERSION_CONFLICT", "会话已存在。")

    def get(self, owner_key, session_id):
        with self._connection() as db:
            row = db.execute(
                "SELECT version,payload FROM clarification_sessions WHERE owner_key=? AND session_id=?",
                (owner_key, session_id),
            ).fetchone()
        if row is None:
            fail("NOT_FOUND", "会话不存在。")
        state = decode_state(row[1])
        if (state.owner_key, state.session_id, state.version) != (owner_key, session_id, row[0]) or type(
            row[0]
        ) is not int:
            fail("STATE_INVALID", "行标识/版本与快照不符。")
        return state

    def compare_and_swap(self, state, *, expected_version):
        check_write_version(state, expected_version)
        payload = encode_state(state)
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE clarification_sessions SET version=?,payload=? WHERE owner_key=? AND session_id=? AND version=?",
                (state.version, payload, state.owner_key, state.session_id, expected_version),
            )
            if cursor.rowcount != 1:
                fail("VERSION_CONFLICT", "会话版本已变化。")
