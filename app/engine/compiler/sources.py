"""确定性的单表/双表来源解析与字段绑定，不搜索多跳路径。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from sqlglot import exp

from app.engine.compiler.errors import (
    DIMENSION_LEVEL_UNRESOLVED,
    JOIN_CARDINALITY_UNSAFE,
    JOIN_RELATION_AMBIGUOUS,
    JOIN_RELATION_MISSING,
    JOIN_SHAPE_UNSUPPORTED,
    JOIN_TABLE_LIMIT,
    JOIN_TYPE_UNSUPPORTED,
    METRIC_OUT_OF_SCOPE,
    TABLE_NOT_IN_SCOPE,
    UNKNOWN_DIMENSION,
    UNKNOWN_SOURCE,
    CompileError,
)
from app.engine.scope import Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef, LevelDef
from app.engine.semantic.models import Intent, MetricDef, RelationDef


@dataclass(frozen=True, slots=True)
class SourcePlan:
    tables: tuple[str, ...]
    aliases: Mapping[str, str]
    relation: RelationDef | None = None

    @property
    def primary(self) -> str:
        return self.tables[0]

    @property
    def joined(self) -> bool:
        return self.relation is not None

    def alias_for(self, table: str = "") -> str:
        """空来源表示主表；物理表按完整名字匹配，不剥离 schema。"""
        return self.aliases[(table or self.primary).casefold()]

    def join_expression(self) -> exp.Join | None:
        relation = self.relation
        if relation is None:
            return None
        condition = exp.EQ(
            this=exp.column(relation.left_column, table=self.alias_for(relation.left_table)),
            expression=exp.column(relation.right_column, table=self.alias_for(relation.right_table)),
        )
        args = {"side": "LEFT"} if relation.join_type == "LEFT" else {"kind": "INNER"}
        return exp.Join(
            this=exp.alias_(exp.to_table(self.tables[1]), self.alias_for(self.tables[1])),
            on=condition,
            **args,
        )


def dimension_level(catalog: SemanticCatalog, name: str) -> tuple[DimensionDef, LevelDef]:
    dimension = catalog.dimension(name)
    if dimension is None:
        raise CompileError("symbol_table", UNKNOWN_DIMENSION, f"语义层里没有维度「{name}」。")
    try:
        _, level = dimension.resolve_level()
    except KeyError as exc:
        raise CompileError("intent", DIMENSION_LEVEL_UNRESOLVED, str(exc)) from exc
    return dimension, level


def resolve_sources(
    intent: Intent,
    metric: MetricDef,
    catalog: SemanticCatalog,
    *,
    scope: Scope | None,
    alias: str,
) -> SourcePlan:
    sources = catalog.metric_tables(metric)
    if not sources:
        raise CompileError("symbol_table", UNKNOWN_SOURCE, f"指标「{metric.name}」没有配置来源表。")
    # 有序去重。Scope 与关系声明顺序都不能改变主表。
    tables = dict.fromkeys(table.casefold() for table in sources)
    for name in (*intent.dimensions, *(f.dimension for f in intent.filters if f.value is not None)):
        _, level = dimension_level(catalog, name)
        if level.table:
            tables.setdefault(level.table.casefold(), None)
    if len(tables) > 2:
        raise CompileError("type_check", JOIN_TABLE_LIMIT, "当前只支持至多两张表，不自动搜索关联路径。")
    names = tuple(tables)
    if scope is not None:
        for i, table in enumerate(names):
            if not scope.allows(table):
                code = METRIC_OUT_OF_SCOPE if i == 0 else TABLE_NOT_IN_SCOPE
                raise CompileError("scope", code, f"来源表 {table} 不在当前可见范围内。")
    secondary_alias = "t2" if alias.casefold() != "t2" else "t3"
    aliases = MappingProxyType(dict(zip(names, (alias, secondary_alias), strict=False)))
    relation = None
    if len(names) == 2:
        if (
            intent.source_mode != "plain"
            or intent.comparison is not None
            or intent.top_n is not None
            or intent.top_n_grain is not None
        ):
            raise CompileError(
                "intent", JOIN_SHAPE_UNSUPPORTED, "双表 JOIN 暂不与对比、Top N 或报表分支组合。"
            )
        relation = _resolve_relation(catalog, *names)
    return SourcePlan(tables=names, aliases=aliases, relation=relation)


def _resolve_relation(catalog: SemanticCatalog, primary: str, secondary: str) -> RelationDef:
    candidates = [r for r in catalog.relations if r.connects(primary, secondary)]
    if not candidates:
        raise CompileError(
            "symbol_table", JOIN_RELATION_MISSING, f"没有配置 {primary} 与 {secondary} 的关联关系。"
        )
    if len(candidates) != 1:
        raise CompileError(
            "type_check", JOIN_RELATION_AMBIGUOUS, "两张表之间存在多条关系，必须先消除配置歧义。"
        )
    relation = candidates[0]
    if relation.join_type not in ("INNER", "LEFT") or any(
        not isinstance(column, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", column) is None
        for column in (relation.left_column, relation.right_column)
    ):
        raise CompileError("type_check", JOIN_TYPE_UNSUPPORTED, "只支持单列等值 INNER/LEFT JOIN。")
    reverse = relation.left_table.casefold() != primary
    if reverse and relation.join_type == "LEFT":
        raise CompileError(
            "type_check", JOIN_TYPE_UNSUPPORTED, "LEFT JOIN 左侧必须是指标主表，不自动反转保留方向。"
        )
    cardinality = relation.cardinality
    if not isinstance(cardinality, str):
        raise CompileError("type_check", JOIN_CARDINALITY_UNSAFE, "关系基数必须是明确的基数编码。")
    if reverse:
        cardinality = {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}.get(
            cardinality, cardinality
        )
    if cardinality not in {"many_to_one", "one_to_one"}:
        raise CompileError(
            "type_check", JOIN_CARDINALITY_UNSAFE, "关联表必须在关联键上唯一，不能重复汇总主表指标。"
        )
    return replace(
        relation,
        left_table=primary,
        right_table=secondary,
        left_column=relation.right_column if reverse else relation.left_column,
        right_column=relation.left_column if reverse else relation.right_column,
        cardinality=cardinality,
    )
