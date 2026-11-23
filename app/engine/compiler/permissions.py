"""编译前的物理列白名单检查，与独立 L4 校验不共享中间产物。"""

from __future__ import annotations

from sqlglot import exp

from app.engine.compiler.errors import COLUMN_NOT_IN_SCOPE, CompileError
from app.engine.compiler.sources import SourcePlan
from app.engine.scope import Scope


def check_column_scope(statement: exp.Select, scope: Scope | None, sources: SourcePlan) -> None:
    """在 Top N 等外层包装前检查，所有引用均指向本次物理表。

    不读校验器 Catalog；这里判断权限而不是列是否存在。
    未配置表白名单表示不限制，显式空白名单表示没有可读列。
    """
    if scope is None or not scope.columns:
        return
    by_alias = {alias.casefold(): table for table, alias in sources.aliases.items()}
    for column in statement.find_all(exp.Column):
        qualifier = ".".join(part for part in (column.catalog, column.db, column.table) if part).casefold()
        table = by_alias.get(qualifier)
        if table is None and qualifier in sources.aliases:
            table = qualifier
        if not qualifier:
            table = sources.primary
        if table is None:
            raise CompileError("scope", COLUMN_NOT_IN_SCOPE, f"列 {column.sql()} 没有可确认的权限来源。")
        allowed = scope.columns_for(table)
        if allowed is not None and (
            isinstance(column.this, exp.Star) or column.name.casefold() not in allowed
        ):
            raise CompileError("scope", COLUMN_NOT_IN_SCOPE, f"列 {table}.{column.name} 不在可见列范围内。")
    # COUNT(*) 不引用具体列，但显式空表白名单不能通过它暴露记录数量。
    if any(True for _ in statement.find_all(exp.Star)):
        for table in sources.tables:
            if scope.columns_for(table) is not None:
                raise CompileError("scope", COLUMN_NOT_IN_SCOPE, f"表 {table} 的列权限不允许通配符查询。")
