"""预计算报表编译：固定粒度直接读取，逐分区校验后 UNION ALL。"""

from __future__ import annotations

from collections.abc import Mapping

from sqlglot import exp

from app.engine.compiler.conditions import build_filter_condition, build_policy_conditions
from app.engine.compiler.errors import (
    AST_INVALID,
    REPORT_CONFIG_INVALID,
    REPORT_FILTER_UNSUPPORTED,
    REPORT_GRAIN_MISMATCH,
    REPORT_PARTITION_MISSING,
    REPORT_SHAPE_UNSUPPORTED,
    REPORT_SOURCE_MISSING,
    TABLE_NOT_IN_SCOPE,
    UNKNOWN_DIMENSION,
    CompileError,
)
from app.engine.compiler.permissions import check_column_scope
from app.engine.compiler.report_time import resolve_report_months
from app.engine.compiler.sources import SourcePlan
from app.engine.scope import Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef
from app.engine.semantic.models import Intent, MetricDef
from app.engine.semantic.report import ReportDef


def compile_report(
    intent: Intent,
    metric: MetricDef,
    catalog: SemanticCatalog,
    *,
    scope: Scope | None,
    alias: str,
    dialect: str,
) -> exp.Query:
    if intent.comparison is not None or intent.top_n is not None or intent.top_n_grain is not None:
        raise CompileError("intent", REPORT_SHAPE_UNSUPPORTED, "报表不支持对比或 Top N，不能退回明细计算。")
    if not isinstance(catalog.reports, Mapping):
        raise CompileError("type_check", REPORT_CONFIG_INVALID, "报表目录必须为映射。")
    if metric.code not in catalog.reports:
        raise CompileError("symbol_table", REPORT_SOURCE_MISSING, f"指标「{metric.name}」未登记报表来源。")
    report = catalog.reports[metric.code]
    if not isinstance(report, ReportDef) or report.metric_code != metric.code:
        raise CompileError("type_check", REPORT_CONFIG_INVALID, "报表定义与指标索引不一致。")
    try:
        report.validate(metric_codes=catalog.metrics, dimension_codes=catalog.dimensions)
    except ValueError as exc:
        raise CompileError("type_check", REPORT_CONFIG_INVALID, str(exc)) from exc

    dimensions: dict[str, DimensionDef] = {}
    for name in intent.dimensions:
        dimension = _dimension(catalog, name)
        dimensions.setdefault(dimension.code, dimension)
    if set(dimensions) != set(report.dimensions):
        raise CompileError(
            "intent", REPORT_GRAIN_MISMATCH, "请求维度必须与报表固定粒度完全一致，不能再次汇总。"
        )
    for item in intent.filters:
        if item.value is None:
            continue
        dimension = _dimension(catalog, item.dimension)
        if dimension.code not in report.dimensions:
            raise CompileError(
                "intent", REPORT_GRAIN_MISMATCH, "过滤维度不属于本报表粒度，不能隐式关联明细。"
            )
        if item.operator not in {"eq", "in"}:
            raise CompileError("intent", REPORT_FILTER_UNSUPPORTED, "报表过滤仅支持 eq/in。")

    months = resolve_report_months(intent.time_range)
    # 先解析所有来源和表权限，不允许缺月时仅返回可用月份。
    tables: list[str] = []
    for month in months:
        table = report.partitions.get(month)
        if table is None:
            raise CompileError("symbol_table", REPORT_PARTITION_MISSING, f"报表未登记 {month} 的物理来源。")
        if scope is not None and not scope.allows(table):
            raise CompileError("scope", TABLE_NOT_IN_SCOPE, f"报表分表 {table} 不在可见范围内。")
        tables.append(table)

    branches: list[exp.Select] = []
    for index, (month, table) in enumerate(zip(months, tables, strict=True)):
        branch_alias = alias if index == 0 else f"{alias}_{index + 1}"
        sources = SourcePlan((table,), {table.casefold(): branch_alias})
        projections: list[exp.Expression] = [
            exp.alias_(exp.column(report.month_column, table=branch_alias), "stat_month")
        ]
        for code in dimensions:
            level = report.dimensions[code]
            projections.append(exp.column(level.group_by, table=branch_alias))
            if level.display and level.display.casefold() != level.group_by.casefold():
                projections.append(exp.column(level.display, table=branch_alias))
        projections.append(exp.alias_(exp.column(report.value_column, table=branch_alias), metric.code))
        conditions: list[exp.Expression] = [
            exp.EQ(
                this=exp.column(report.month_column, table=branch_alias),
                expression=exp.Literal.string(month),
            )
        ]
        for item in intent.filters:
            if item.value is None:
                continue
            dimension = _dimension(catalog, item.dimension)
            level = report.dimensions[dimension.code]
            conditions.append(
                build_filter_condition(
                    item, catalog, dimension, exp.column(level.group_by, table=branch_alias)
                )
            )
        conditions.extend(build_policy_conditions(scope, alias=branch_alias, sources=sources))
        condition = conditions[0]
        for extra in conditions[1:]:
            condition = exp.And(this=condition, expression=extra)
        branch = exp.Select(
            expressions=projections,
            from_=exp.From(this=exp.alias_(exp.to_table(table), branch_alias)),
            where=exp.Where(this=condition),
        )
        if not branch.args.get("from_"):
            raise CompileError("internal", AST_INVALID, "报表分支缺少 FROM 子句。")
        check_column_scope(branch, scope, sources)
        branches.append(branch)

    query: exp.Query = branches[0]
    for branch in branches[1:]:
        query = exp.Union(this=query, expression=branch, distinct=False)
    query = query.order_by("stat_month", *(report.dimensions[code].group_by for code in dimensions))
    try:
        query.sql(dialect=dialect)
    except Exception as exc:
        raise CompileError("internal", AST_INVALID, f"报表产物无法渲染：{exc}") from exc
    return query


def _dimension(catalog: SemanticCatalog, name: str) -> DimensionDef:
    dimension = catalog.dimension(name)
    if dimension is None:
        raise CompileError("symbol_table", UNKNOWN_DIMENSION, f"语义层里没有维度「{name}」。")
    return dimension
