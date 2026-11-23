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

from app.engine.compiler.errors import JOIN_FILTER_UNSUPPORTED, UNKNOWN_CODE, CompileError
from app.engine.compiler.sources import SourcePlan, dimension_level
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef
from app.engine.semantic.models import Filter, Intent


def build_policy_conditions(
    scope: Scope | None, *, alias: str, sources: SourcePlan | None = None
) -> list[exp.Expression]:
    """权限条件。**fail-closed**：一条策略没有取值时生成恒假条件，而不是跳过。"""
    if scope is None or not scope.row_policies:
        return []

    conditions: list[exp.Expression] = []
    for row_policy in scope.row_policies:
        policy_alias = alias
        if sources is not None:
            policy_alias = sources.aliases.get(row_policy.table.casefold())
            if policy_alias is None:
                continue  # 非本次查询的表，不能为应用策略而偷偷新增关联。
        conditions.append(_policy_condition(row_policy, alias=policy_alias))
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
    sources: SourcePlan | None = None,
) -> list[exp.Expression]:
    """业务过滤条件。

    维度 -> 口径 -> 物理列 -> 码值，四步全部确定性。
    ``Intent.filters[].value`` 的契约是"数据库存储值"；若语义层配了码值表，
    这里再做一次幂等翻译（已经翻译过就原样返回）。
    """
    conditions: list[exp.Expression] = []
    for item in intent.filters:
        raw_value = item.value
        if raw_value is None:
            continue
        dimension, level = dimension_level(catalog, item.dimension)
        if sources is not None and sources.joined and item.operator not in {"eq", "in"}:
            raise CompileError(
                "intent", JOIN_FILTER_UNSUPPORTED, "双表过滤当前只支持 eq/in，不自动降级运算符。"
            )
        column_alias = sources.alias_for(level.table) if sources is not None else alias
        column = exp.column(level.group_by, table=column_alias)

        conditions.append(build_filter_condition(item, catalog, dimension, column))
    return conditions


def build_filter_condition(
    item: Filter,
    catalog: SemanticCatalog,
    dimension: DimensionDef,
    column: exp.Column,
) -> exp.Expression:
    """复用码值翻译与谓词构造；物理绑定和运算符边界由各来源编译器负责。"""
    if item.operator == "in":
        raw = item.value
        values = tuple(raw) if isinstance(raw, (list, tuple, set)) else (raw,)
        if not values:
            return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
        literals = [exp.Literal.string(_stored_value(catalog, dimension, value)) for value in values]
        if len(literals) == 1:
            return exp.EQ(this=column, expression=literals[0])
        return exp.In(this=column, expressions=literals)
    return exp.EQ(this=column, expression=exp.Literal.string(_stored_value(catalog, dimension, item.value)))


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
