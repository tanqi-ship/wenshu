"""占比：底层保留上下文和权限，整体计算完成后才筛选展示对象。"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import replace

from sqlglot import exp

from app.engine.compiler.conditions import build_filter_condition, build_policy_conditions
from app.engine.compiler.errors import (
    AST_INVALID,
    METRIC_OUT_OF_SCOPE,
    SHARE_CONTEXT_UNRESOLVED,
    SHARE_DIMENSION_MISMATCH,
    SHARE_DIMENSION_REQUIRED,
    SHARE_FILTER_UNSUPPORTED,
    SHARE_METRIC_NOT_ADDITIVE,
    SHARE_OUTPUT_COLLISION,
    SHARE_SHAPE_UNSUPPORTED,
    SHARE_TIME_UNSUPPORTED,
    TIME_UNRESOLVED,
    UNKNOWN_SOURCE,
    CompileError,
)
from app.engine.compiler.metrics import parse_metric_formula
from app.engine.compiler.permissions import check_column_scope
from app.engine.compiler.sources import SourcePlan, dimension_level
from app.engine.compiler.time import build_time_condition
from app.engine.scope import Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Intent, MetricDef, ShareSpec
from app.engine.validation.policy import ValidationPolicy


def compile_share(
    intent: Intent,
    metric: MetricDef,
    catalog: SemanticCatalog,
    *,
    scope: Scope | None,
    policy: ValidationPolicy,
    alias: str,
    dialect: str,
) -> exp.Select:
    if (
        not isinstance(intent.share, ShareSpec)
        or intent.source_mode != "plain"
        or intent.comparison is not None
        or intent.top_n is not None
        or intent.top_n_grain is not None
    ):
        raise CompileError(
            "intent", SHARE_SHAPE_UNSUPPORTED, "占比仅支持普通明细，不能与报表、对比或 Top N 混用。"
        )
    if intent.org:
        raise CompileError(
            "intent", SHARE_CONTEXT_UNRESOLVED, "占比的机构条件必须明确规范到 filters，不能忽略 org。"
        )
    if not isinstance(intent.share.dimension, str) or not intent.share.dimension.strip():
        raise CompileError("intent", SHARE_DIMENSION_REQUIRED, "占比必须明确拆分维度。")
    _check_time(intent)
    intent = replace(
        intent, time_range=replace(intent.time_range, points=tuple(sorted(set(intent.time_range.points))))
    )
    split, level = dimension_level(catalog, intent.share.dimension)
    dimensions = {dimension_level(catalog, name)[0].code for name in intent.dimensions}
    if dimensions != {split.code}:
        raise CompileError(
            "intent", SHARE_DIMENSION_MISMATCH, "首版占比只支持与 share.dimension 一致的一个维度。"
        )

    tables = tuple(dict.fromkeys(table.casefold() for table in catalog.metric_tables(metric)))
    if not tables:
        raise CompileError("symbol_table", UNKNOWN_SOURCE, f"指标「{metric.name}」没有来源表。")
    if len(tables) != 1:
        raise CompileError("intent", SHARE_SHAPE_UNSUPPORTED, "占比只支持一个实际物理来源，不能丢弃其他表。")
    table = tables[0]
    _check_source(level.table, table)
    if scope is not None and not scope.allows(table):
        raise CompileError("scope", METRIC_OUT_OF_SCOPE, f"来源表 {table} 不在可见范围内。")
    sources = SourcePlan((table,), {table: alias})
    grouped_alias, totals_alias = _aliases(alias, table)
    conditions = [build_time_condition(intent.time_range, alias=alias, policy=policy, dialect=dialect)]
    display_filters: list[exp.Expression] = []
    for item in intent.filters:
        if item.value is None:
            continue
        dimension, binding = dimension_level(catalog, item.dimension)
        _check_source(binding.table, table)
        if item.operator not in {"eq", "in"}:
            raise CompileError("intent", SHARE_FILTER_UNSUPPORTED, "占比业务过滤只支持 eq/in。")
        if dimension.code == split.code:
            target, column_alias = display_filters, totals_alias
        else:
            if binding.group_by.casefold() == level.group_by.casefold():
                raise CompileError(
                    "intent", SHARE_FILTER_UNSUPPORTED, "上下文维度与占比编号列重叠，不能猜测分母边界。"
                )
            target, column_alias = conditions, alias
        target.append(
            build_filter_condition(item, catalog, dimension, exp.column(binding.group_by, table=column_alias))
        )
    conditions.extend(build_policy_conditions(scope, alias=alias, sources=sources))

    metric_expression = _metric(metric, table=table, alias=alias, dialect=dialect)
    group = exp.column(level.group_by, table=alias)
    projections = [exp.alias_(group.copy(), level.group_by)]
    if level.display and level.display.casefold() != level.group_by.casefold():
        projections.append(exp.alias_(exp.Max(this=exp.column(level.display, table=alias)), level.display))
    projections.append(exp.alias_(metric_expression, metric.code))
    outputs = [item.alias_or_name for item in projections]
    names = [name.casefold() for name in (*outputs, "total_value", "share")]
    if len(set(names)) != len(names):
        raise CompileError("type_check", SHARE_OUTPUT_COLLISION, "占比输出与保留列 total_value/share 重名。")
    aggregate = (
        exp.Select(expressions=projections)
        .from_(exp.alias_(exp.to_table(table), alias))
        .where(_and(conditions))
        .group_by(group)
    )
    check_column_scope(aggregate, scope, sources)
    totals = (
        exp.Select(expressions=[exp.column(name, table=grouped_alias) for name in outputs])
        .from_(aggregate.subquery(grouped_alias))
        .select(
            exp.alias_(
                exp.Window(this=exp.Sum(this=exp.column(metric.code, table=grouped_alias)), over="OVER"),
                "total_value",
            )
        )
    )
    ratio = exp.Div(
        this=exp.Mul(this=exp.Literal.number("1.0"), expression=exp.column(metric.code, table=totals_alias)),
        expression=exp.Nullif(
            this=exp.column("total_value", table=totals_alias), expression=exp.Literal.number(0)
        ),
        typed=True,
        safe=False,
    )
    result = exp.Select(
        expressions=[
            *(exp.column(name, table=totals_alias) for name in (*outputs, "total_value")),
            exp.alias_(ratio, "share"),
        ]
    ).from_(totals.subquery(totals_alias))
    if display_filters:
        result = result.where(_and(display_filters))
    result = result.order_by(exp.column(level.group_by, table=totals_alias))
    if any(not select.args.get("from_") for select in result.find_all(exp.Select)):
        raise CompileError("internal", AST_INVALID, "占比分层查询缺少 FROM。")
    try:
        result.sql(dialect=dialect)
    except Exception as exc:
        raise CompileError("internal", AST_INVALID, f"占比产物无法渲染：{exc}") from exc
    return result


def _check_time(intent: Intent) -> None:
    time = intent.time_range
    if time is None:
        raise CompileError("intent", TIME_UNRESOLVED, "占比需要明确月末时点。")
    if time.grain != "month_end" or time.start is not None or time.end is not None:
        raise CompileError("intent", SHARE_TIME_UNSUPPORTED, "首版占比只支持明确的单个月末点，不接受区间。")
    points = set(time.points)
    if not points:
        raise CompileError("intent", TIME_UNRESOLVED, "占比缺少月末日期。")
    if len(points) != 1 or any(p.day != monthrange(p.year, p.month)[1] for p in points):
        raise CompileError("intent", SHARE_TIME_UNSUPPORTED, "占比必须是单个真实月末，不能混合快照。")


def _metric(metric: MetricDef, *, table: str, alias: str, dialect: str) -> exp.Expression:
    expression = parse_metric_formula(metric, alias=alias, dialect=dialect)
    if (
        metric.is_percentage
        or not isinstance(expression, exp.Sum)
        or expression.find(exp.Distinct, exp.Window, exp.Query, exp.Star) is not None
        or len(list(expression.find_all(exp.AggFunc))) != 1
    ):
        raise CompileError(
            "type_check",
            SHARE_METRIC_NOT_ADDITIVE,
            "占比仅支持普通可加总 SUM，不对已有比例或 DISTINCT 求整体。",
        )
    for column in expression.find_all(exp.Column):
        qualifier = ".".join(part for part in (column.catalog, column.db, column.table) if part).casefold()
        if qualifier not in {table.casefold(), alias.casefold()}:
            raise CompileError(
                "intent", SHARE_SHAPE_UNSUPPORTED, f"指标列 {column.sql()} 不能确认来自唯一主表。"
            )
        column.set("table", exp.to_identifier(alias))
        column.set("db", None)
        column.set("catalog", None)
    return expression


def _check_source(source: str, table: str) -> None:
    if source and source.casefold() != table.casefold():
        raise CompileError("intent", SHARE_SHAPE_UNSUPPORTED, "占比维度或过滤需要外部表，首版不支持 JOIN。")


def _aliases(alias: str, table: str) -> tuple[str, str]:
    occupied = {alias.casefold(), *(part.casefold() for part in table.split("."))}
    result = []
    for base in ("share_groups", "share_totals"):
        candidate, index = base, 2
        while candidate.casefold() in occupied:
            candidate, index = f"{base}_{index}", index + 1
        occupied.add(candidate.casefold())
        result.append(candidate)
    return result[0], result[1]


def _and(conditions: list[exp.Expression]) -> exp.Expression:
    result = conditions[0]
    for condition in conditions[1:]:
        result = exp.And(this=result, expression=condition)
    return result
