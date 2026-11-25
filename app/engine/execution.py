"""L5：只读执行引擎已经校验的 SQLite 查询，不承担数据授权。"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

_QUERY_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ})
_QUERY_FUNCTIONS = frozenset({"sum", "max", "min", "count", "avg", "nullif", "coalesce", "row_number"})
_DENIED = frozenset({sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM})
_UNAVAILABLE = frozenset({sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT})

SQLiteValue = None | int | float | str | bytes


@dataclass(frozen=True, slots=True)
class QueryRows:
    columns: tuple[str, ...]
    rows: tuple[tuple[SQLiteValue, ...], ...]
    truncated: bool


class ExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


def _authorize(action: int, _name: str | None, argument: str | None, *_context: str | None) -> int:
    if action in _QUERY_ACTIONS or (
        action == sqlite3.SQLITE_FUNCTION and argument is not None and argument.lower() in _QUERY_FUNCTIONS
    ):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _sqlite_error(exc: sqlite3.Error) -> ExecutionError:
    primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if primary in _DENIED:
        return ExecutionError("EXECUTION_DENIED", "SQLite 拒绝了该操作。")
    if primary in _UNAVAILABLE:
        return ExecutionError("EXECUTION_DB_UNAVAILABLE", "业务数据库不可用。")
    return ExecutionError("EXECUTION_FAILED", "业务查询执行失败。")


@dataclass(frozen=True, slots=True)
class SQLiteExecutor:
    path: Path
    max_rows: int = 100
    timeout_s: float = 2.0

    def __post_init__(self) -> None:
        if type(self.max_rows) is not int or not 1 <= self.max_rows <= 1000:
            raise ExecutionError("EXECUTION_INVALID_CONFIG", "返回行数上限必须是 1 到 1000 的整数。")
        if (
            type(self.timeout_s) not in (float, int)
            or not 0 < self.timeout_s <= 2_147_483
            or not math.isfinite(self.timeout_s)
        ):
            raise ExecutionError("EXECUTION_INVALID_CONFIG", "查询预算必须为有效的正秒数。")

    def execute(self, sql: str) -> QueryRows:
        try:
            path = self.path.resolve()
            if not path.is_file():
                raise ExecutionError("EXECUTION_DB_UNAVAILABLE", "业务数据库不可用。")
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=self.timeout_s)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            raise ExecutionError("EXECUTION_DB_UNAVAILABLE", "业务数据库不可用。") from None

        deadline = monotonic() + self.timeout_s
        expired = False
        denied = False

        def check_budget() -> int:
            nonlocal expired
            expired = expired or monotonic() >= deadline
            return int(expired)

        def authorize(action: int, name: str | None, argument: str | None, *context: str | None) -> int:
            nonlocal denied
            decision = _authorize(action, name, argument, *context)
            denied = denied or decision == sqlite3.SQLITE_DENY
            return decision

        try:
            with closing(db), closing(db.cursor()) as cursor:
                cursor.execute("PRAGMA query_only = ON")
                cursor.execute("PRAGMA trusted_schema = OFF")
                db.set_authorizer(authorize)
                db.set_progress_handler(check_budget, 1000)
                cursor.execute(sql)
                if cursor.description is None:
                    raise ExecutionError("EXECUTION_DENIED", "业务执行器只接受返回数据的查询。")
                columns = tuple(column[0] for column in cursor.description)
                rows = cursor.fetchmany(self.max_rows + 1)
                if check_budget():
                    raise ExecutionError("EXECUTION_TIMEOUT", "业务查询超过时间预算。")
                return QueryRows(columns, tuple(rows[: self.max_rows]), len(rows) > self.max_rows)
        except sqlite3.Error as exc:
            if denied:
                raise ExecutionError("EXECUTION_DENIED", "SQLite 拒绝了该操作。") from None
            if expired and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
                raise ExecutionError("EXECUTION_TIMEOUT", "业务查询超过时间预算。") from None
            raise _sqlite_error(exc) from None
