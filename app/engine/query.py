"""M3.4：在会话补齐槽位后接通编译、独立校验与只读执行。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.engine.compiler import compile_sql
from app.engine.compiler.errors import (
    COLUMN_NOT_IN_SCOPE,
    METRIC_OUT_OF_SCOPE,
    TABLE_NOT_IN_SCOPE,
    UNKNOWN_METRIC,
    CompileError,
)
from app.engine.validation import validate_sql

if TYPE_CHECKING:
    from app.engine.clarify.session import ClarificationService, SessionResult
    from app.engine.execution import QueryRows, SQLiteExecutor
    from app.engine.scope import Scope
    from app.engine.semantic.catalog import SemanticCatalog
    from app.engine.validation.catalog import Catalog
    from app.engine.validation.problems import Problem


class QueryValidationError(Exception):
    def __init__(self, problems: tuple[Problem, ...]) -> None:
        self.problems = problems
        super().__init__("[QUERY_VALIDATION_FAILED] " + "；".join(problem.render() for problem in problems))


@dataclass(frozen=True, slots=True)
class QueryAnswer:
    session: SessionResult
    result: QueryRows


@dataclass(frozen=True, slots=True)
class QueryRejection:
    code: Literal["UNANSWERABLE", "OUT_OF_SCOPE"]
    message: str
    session_id: str
    version: int


@dataclass(frozen=True, slots=True)
class QueryService:
    clarification: ClarificationService
    semantic: SemanticCatalog
    physical: Catalog
    executor: SQLiteExecutor

    async def start(
        self, question: str, *, owner_key: str, catalog_revision: str, scope: Scope
    ) -> SessionResult | QueryAnswer | QueryRejection:
        session = await self.clarification.start(
            question, self.semantic, owner_key=owner_key, catalog_revision=catalog_revision
        )
        return await self._finish(session, scope)

    async def reply(
        self,
        session_id: str,
        answer: str,
        *,
        owner_key: str,
        catalog_revision: str,
        expected_version: int,
        scope: Scope,
    ) -> SessionResult | QueryAnswer | QueryRejection:
        session = await self.clarification.reply(
            session_id,
            answer,
            self.semantic,
            owner_key=owner_key,
            catalog_revision=catalog_revision,
            expected_version=expected_version,
        )
        return await self._finish(session, scope)

    async def _finish(
        self, session: SessionResult, scope: Scope
    ) -> SessionResult | QueryAnswer | QueryRejection:
        if session.status != "slots_complete" or session.intent is None:
            return session
        # ``None`` would mean unrestricted to L3. Only an explicit Scope is valid here.
        if scope is None:
            raise TypeError("业务查询必须显式提供 Scope。")
        try:
            sql = compile_sql(session.intent, self.semantic, scope=scope, dialect="sqlite")
        except CompileError as exc:
            if (exc.stage, exc.code) == ("symbol_table", UNKNOWN_METRIC):
                return QueryRejection(
                    "UNANSWERABLE", "当前不支持查询该指标。", session.session_id, session.version
                )
            if exc.stage == "scope" and exc.code in (
                METRIC_OUT_OF_SCOPE,
                TABLE_NOT_IN_SCOPE,
                COLUMN_NOT_IN_SCOPE,
            ):
                return QueryRejection(
                    "OUT_OF_SCOPE", "当前权限无法完成该查询。", session.session_id, session.version
                )
            raise
        validation = validate_sql(
            sql, catalog=self.physical, scope=scope, intent=session.intent, dialect="sqlite"
        )
        if validation.problems:
            raise QueryValidationError(tuple(validation.problems))
        result = await asyncio.to_thread(self.executor.execute, sql)
        return QueryAnswer(session, result)
