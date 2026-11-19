"""本次提问的边界：可见范围 + 行级策略。

**这不是一套权限系统，是编译器的一个输入**（ADR-0004）。

三件事刻意的设计：

1. **传进来，不查库。** 引擎不认识用户、角色、登录。测试里直接构造一个
   ``Scope`` 就能测全部权限行为，不需要任何用户体系。
2. **纯数据，不 import sqlglot。** 让配置代码和测试可以只要一份 ``Scope``。
3. **业务过滤与权限过滤分开。** 用户能看见/能改 ``Intent.filters``，
   **绝不能**看见/改这里的 ``row_policies``。合成时才合并，合并后不允许下游修改。
   （askdb 用 ``rebuild_sql_service.py:145`` 的 ``_authorized_rebuild_scope()``
   打补丁解决这个问题；本项在结构上避免。）
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

PolicyOperator = Literal["eq", "in"]


@dataclass(frozen=True, slots=True)
class RowPolicy:
    """一条行级过滤。

    由权限产生，**不由用户意图产生**。用户在界面上"改条件重跑"时，
    这些条件必须原样保留（这是 askdb 花了一个补丁才做到的事）。
    """

    table: str
    column: str
    values: tuple[str, ...] = ()
    operator: PolicyOperator = "eq"


@dataclass(frozen=True, slots=True)
class Scope:
    """本次提问允许使用的表和列。

    ``columns`` 为空表示**不限制列**（与"限制成空集"不同）。
    需要"一个列都看不到"时请显式传 ``{"t": frozenset()}``。
    """

    tables: frozenset[str] = frozenset()
    #: 表名 -> 可见列（全部小写）
    columns: Mapping[str, frozenset[str]] = field(default_factory=dict)
    row_policies: tuple[RowPolicy, ...] = ()

    @classmethod
    def of(
        cls,
        names: list[str] | tuple[str, ...] | set[str],
        *,
        columns: Mapping[str, frozenset[str]] | None = None,
        row_policies: tuple[RowPolicy, ...] = (),
    ) -> Scope:
        return cls(
            tables=frozenset(name.lower() for name in names if name),
            columns={key.lower(): value for key, value in (columns or {}).items()},
            row_policies=row_policies,
        )

    def allows(self, table: str, schema: str | None = None) -> bool:
        lowered = table.lower()
        if lowered in self.tables:
            return True
        if schema:
            return f"{schema.lower()}.{lowered}" in self.tables
        return False

    def columns_for(self, table: str) -> frozenset[str] | None:
        """该表可见的列；``None`` 表示不限制。"""
        if not self.columns:
            return None
        return self.columns.get(table.lower())

    def policies_for(self, table: str) -> tuple[RowPolicy, ...]:
        lowered = table.lower()
        return tuple(item for item in self.row_policies if item.table.lower() == lowered)

    def __bool__(self) -> bool:
        return bool(self.tables)
