"""WHERE 条件的两个来源，**必须分开构造**（ADR-0004）。

=====  =========================  ==========================  ==========
来源   谁产生                      用户能看见吗                 用户能改吗
=====  =========================  ==========================  ==========
业务   ``Intent.filters``         能（出现在 SQL 预览里）       能（追问、改条件重跑）
权限   ``Scope.row_policies``     不能                        不能
=====  =========================  ==========================  ==========

合成顺序固定：**先业务、后权限**。合成后不允许下游修改权限那一段。

为什么要强调：askdb 的"改条件重跑"曾经可以把权限条件一起改掉，
最后靠 ``rebuild_sql_service.py:145`` 的 ``_authorized_rebuild_scope()`` 打补丁。
本项目在结构上避免这个问题 —— 权限条件只在一个函数里产生，只拼一次。
"""

from __future__ import annotations

from sqlglot import exp

from app.engine.compiler.errors import UNKNOWN_CODE, UNKNOWN_DIMENSION, CompileError
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef
from app.engine.semantic.models import Intent


def build_policy_conditions(scope: Scope | None, *, alias: str) -> list[exp.Expression]:
    """权限条件。**fail-closed**：一条策略没有取值时生成恒假条件，而不是跳过。"""
    if scope is None or not scope.row_policies:
        return []

    conditions: list[exp.Expression] = []
    for row_policy in scope.row_policies:
        conditions.append(_policy_condition(row_policy, alias=alias))
    return conditions


def _policy_condition(row_policy: RowPolicy, *, alias: str) -> exp.Expression:
    column = exp.column(row_policy.column, table=alias)
    values = tuple(str(value) for value in row_policy.values if str(value))

    if not values:
        # 显式"看不到任何行"。静默跳过会变成越权，这是最危险的一种退化。
        return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))

    literals = [exp.Literal.string(value) for value in values]
    if row_policy.operator == "in" or len(literals) > 1:
        return exp.In(this=column, expressions=literals)
    return exp.EQ(this=column, expression=literals[0])


def build_business_conditions(
    intent: Intent,
    catalog: SemanticCatalog,
    *,
    alias: str,
) -> list[exp.Expression]:
    """业务过滤条件。

    维度 -> 口径 -> 物理列 -> 码值，四步全部确定性。
    ``Intent.filters[].value`` 的契约是"数据库存储值"；若语义层配了码值表，
    这里再做一次幂等翻译（已经翻译过就原样返回）。
    """
    conditions: list[exp.Expression] = []
    for item in intent.filters:
        dimension = catalog.dimension(item.dimension)
        if dimension is None:
            raise CompileError(
                "symbol_table",
                UNKNOWN_DIMENSION,
                f"语义层里没有维度「{item.dimension}」。",
            )

        try:
            _level_key, level = dimension.resolve_level()
        except KeyError as exc:
            raise CompileError("intent", "DIMENSION_LEVEL_UNRESOLVED", str(exc)) from exc

        raw_value = item.value
        if raw_value is None:
            continue

        column = exp.column(level.group_by, table=alias)

        if item.operator == "in":
            # 列表和单值都要支持：``in`` 只写一个值时退化成一个 EQ，形状更简单。
            raw_values = tuple(raw_value) if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
            literals = [exp.Literal.string(_stored_value(catalog, dimension, value)) for value in raw_values]
            if len(literals) == 1:
                conditions.append(exp.EQ(this=column, expression=literals[0]))
            else:
                conditions.append(exp.In(this=column, expressions=literals))
            continue

        conditions.append(
            exp.EQ(this=column, expression=exp.Literal.string(_stored_value(catalog, dimension, raw_value)))
        )
    return conditions


def _stored_value(catalog: SemanticCatalog, dimension: DimensionDef, raw_value: object) -> str:
    """业务值 → 数据库存储值。**fail-closed**。

    三条规则，顺序即优先级：

    1. 码值表里有 → 返回存储值
    2. 码值表里没有、但该维度**配了**码值表 → 抛 ``UNKNOWN_CODE``
    3. 该维度**没配**码值表 → 原样透传（契约上值就该是存储值）

    第 2 条是 fail-closed：语义层既然声明了码值表，就说明这个维度**必须**
    靠精确等值（ADR-0002，如机构名这类高基数值）。此时静默透传会得到
    ``org_no = '某某支行'`` —— 永远匹配不到行，结果是 0 而不是错误。
    """
    stored = catalog.code(dimension.code, raw_value)
    if stored is not None:
        return stored
    if catalog.code_map.get(dimension.code):
        raise CompileError(
            "symbol_table",
            UNKNOWN_CODE,
            f"维度「{dimension.name}」的取值「{raw_value}」不在码值表中。",
        )
    return str(raw_value)
