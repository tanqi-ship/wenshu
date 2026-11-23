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

    不传 Scope 表示不限制；显式空 Scope 表示没有可见表，编译器和校验器均拒绝。
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
            columns={
                key.lower(): frozenset(name.lower() for name in value)
                for key, value in (columns or {}).items()
            },
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


# ---------------------------------------------------------------------------
# 从配置构造（黄金集 / 评测集用）
# ---------------------------------------------------------------------------

#: 允许的键。与 :func:`app.engine.semantic.models.intent_from_mapping` 的原则一致：
#: **未知键直接报错，绝不静默忽略**。配置里把 ``row_policies`` 手滑写成
#: ``row_policy``，若被忽略，黄金集会"通过"——但它测的东西已经悄悄变了。
_SCOPE_KEYS = frozenset({"tables", "columns", "row_policies"})
_ROW_POLICY_KEYS = frozenset({"table", "column", "operator", "values"})
_POLICY_OPERATORS = frozenset({"eq", "in"})


def _reject_unknown_keys(payload: Mapping[str, object], allowed: frozenset[str], what: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"{what} 里有不认识的键：{', '.join(unknown)}。允许的键：{', '.join(sorted(allowed))}。"
        )


def _as_mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} 应该是一个映射，当前是 {type(value).__name__}。")
    return value


def _row_policy_from_mapping(raw: object) -> RowPolicy:
    payload = _as_mapping(raw, "row_policies[]")
    _reject_unknown_keys(payload, _ROW_POLICY_KEYS, "row_policies[]")

    table = str(payload.get("table") or "").strip()
    column = str(payload.get("column") or "").strip()
    if not table:
        raise ValueError("row_policies[] 缺少 table。")
    if not column:
        raise ValueError("row_policies[] 缺少 column。")

    operator = str(payload.get("operator") or "eq").strip()
    if operator not in _POLICY_OPERATORS:
        raise ValueError(f"row_policies[].operator 只能是 eq / in，当前是 {operator!r}。")

    values_raw = payload.get("values")
    if values_raw is None:
        values: tuple[str, ...] = ()
    elif isinstance(values_raw, str) or not isinstance(values_raw, (list, tuple)):
        raise ValueError("row_policies[].values 应该是取值列表，不是单个字符串。")
    else:
        values = tuple(str(value) for value in values_raw)

    return RowPolicy(table=table, column=column, operator=operator, values=values)  # type: ignore[arg-type]


def scope_from_mapping(data: Mapping[str, object]) -> Scope:
    """从配置结构构造 :class:`Scope`（黄金集用例读它）。

    结构::

        scope:
          tables: [qa_loan_acc_detail]
          columns:                       # 可选，缺省表示"不限制列"
            qa_loan_acc_detail: [org_no, org_nm]
          row_policies:                  # 可选
            - table: qa_loan_acc_detail
              column: org_no
              operator: in
              values: ["B001", "B002"]

    **一条没有取值的策略是合法的**（``values`` 省略）—— 它表示"这一行都看不到"，
    编译期会变成恒假条件 ``1 = 0``。加载器不许把它当成"没配策略"而丢掉：
    那正是 fail-closed 与静默越权之间的那条线。
    """
    payload = _as_mapping(data, "scope")
    _reject_unknown_keys(payload, _SCOPE_KEYS, "scope")

    tables_raw = payload.get("tables") or ()
    if isinstance(tables_raw, str) or not isinstance(tables_raw, (list, tuple, set, frozenset)):
        raise ValueError("scope.tables 应该是表名列表。")
    tables = tuple(str(name).strip() for name in tables_raw if str(name).strip())
    if not tables:
        raise ValueError("scope.tables 不能为空：一个表都不可见时，不该有这条用例。")

    columns_raw = payload.get("columns") or {}
    columns_payload = _as_mapping(columns_raw, "scope.columns")
    columns: dict[str, frozenset[str]] = {}
    for table, names in columns_payload.items():
        if isinstance(names, str) or not isinstance(names, (list, tuple, set, frozenset)):
            raise ValueError(f"scope.columns[{table}] 应该是列名列表。")
        columns[str(table)] = frozenset(str(name).lower() for name in names)

    policies_raw = payload.get("row_policies") or ()
    if isinstance(policies_raw, Mapping):
        raise ValueError("scope.row_policies 应该是列表。")
    row_policies = tuple(_row_policy_from_mapping(item) for item in policies_raw)

    return Scope.of(list(tables), columns=columns or None, row_policies=row_policies)
