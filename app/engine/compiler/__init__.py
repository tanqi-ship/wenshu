"""编译层。

核心思想：**把"写 SQL"从模型手里拿走。**

    Intent + 语义层 + Scope
        -> sqlglot AST
        -> 方言 SQL

全程零模型调用。好处不只是省 token：

* **可单测**：给定 Intent，断言生成的 AST 结构，不需要跑模型
* **口径一致**：同一个指标永远展开成同一个表达式，不会这次乘 100 下次不乘
* **时间口径集中**："近半年"只有一个定义，不在 5,500 字 prompt 里各说一遍
* **错误提前**：编译不出唯一 AST 时说明意图有歧义，此时应该澄清，
  而不是让模型写一条看起来能跑的 SQL
* **失败不可重试**：纯函数，同样输入永远同样输出（ADR-0005）

实现分五步（详见 ``docs/ARCHITECTURE.md`` 第四节）：

    ① resolve     符号表查找
    ② check       类型检查（M2：MetricDef.allowed_dimensions）
    ③ build       指标公式 / 时间形态 / 维度投影 / 行级策略
    ④ assemble    填 4 个空：expressions / from / where / group_by
    ⑤ render      select.sql(dialect)

**已实现（M1）**：指标公式展开、时间条件（month_end / day / range）、
维度分组（GROUP BY 编号列 + ``MAX(名称列)``）、业务过滤、行级权限注入。
**已实现（M2.1）**：双时点对比 ``delta`` —— 一个 SELECT 内 ``WHERE etl_dt IN (...)``
+ 按拆分列聚合 + 差值列，禁止子查询（见 ``compiler/comparison.py``）。
**已实现（M2.2）**：每月 Top N：月末聚合 + ROW_NUMBER 排名 + 外层筛选。
**未实现（M2）**：同比 / 环比 / 比率、全局 Top N、多表 JOIN、
报表 UNION 分支、占比、维度白名单类型检查。
"""

from __future__ import annotations

from sqlglot import exp

from app.engine.compiler.comparison import build_metric_projections, resolve_comparison_points
from app.engine.compiler.conditions import build_business_conditions, build_policy_conditions
from app.engine.compiler.errors import (
    AST_INVALID,
    METRIC_OUT_OF_SCOPE,
    UNKNOWN_DIMENSION,
    UNKNOWN_METRIC,
    UNKNOWN_SOURCE,
    CompileError,
    CompileStage,
)
from app.engine.compiler.metrics import qualify_columns
from app.engine.compiler.time import build_point_condition, build_time_condition, month_end_range
from app.engine.compiler.topn import resolve_monthly_topn, wrap_monthly_topn
from app.engine.scope import Scope
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Intent, MetricDef
from app.engine.validation.policy import ValidationPolicy

__all__ = [
    "CompileError",
    "CompileStage",
    "compile_intent",
    "compile_sql",
    "month_end_range",
]


def compile_intent(
    intent: Intent,
    catalog: SemanticCatalog,
    *,
    scope: Scope | None = None,
    policy: ValidationPolicy | None = None,
    dialect: str = "sqlite",
    alias: str = "t1",
) -> exp.Select:
    """把一个 ``Intent`` 编译成 ``SELECT`` 语法树。

    抛 :class:`CompileError`，``stage`` 说明该由谁修（见 ``errors.py``）。
    """
    policy = policy or ValidationPolicy()

    # ① resolve —— 符号表查找
    metric = _resolve_metric(intent, catalog)
    table = _primary_table(metric, catalog, scope)

    # ② check —— 每月序列与多时点对比是两种形状，先校验再造节点。
    #    Top N 的时点用于逐月分组；只有对比时点才用于分段聚合。
    monthly_points = resolve_monthly_topn(intent)
    points = resolve_comparison_points(intent) if monthly_points is None else ()

    # ③ build —— 造节点
    if monthly_points is not None:
        time_condition = (
            build_point_condition(
                exp.column(policy.snapshot_column, table=alias), monthly_points, dialect=dialect
            )
            if monthly_points
            else exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
        )
    else:
        time_condition = build_time_condition(
            intent.time_range,
            alias=alias,
            policy=policy,
            dialect=dialect,
        )
    conditions: list[exp.Expression] = [time_condition]
    conditions.extend(build_business_conditions(intent, catalog, alias=alias))
    conditions.extend(build_policy_conditions(scope, alias=alias))

    metric_projections = build_metric_projections(
        metric,
        points=points,
        comparison=intent.comparison,
        alias=alias,
        snapshot_column=policy.snapshot_column,
        dialect=dialect,
    )

    dimension_items = _dimension_items(intent, catalog, alias=alias)

    # ④ assemble —— 填 4 个空
    projections: list[exp.Expression] = []
    group_columns: list[exp.Expression] = []
    for group_column, display_projection in dimension_items:
        projections.append(exp.alias_(group_column, group_column.name))
        if display_projection is not None:
            projections.append(display_projection)
        group_columns.append(group_column)
    if monthly_points is not None:
        snapshot = exp.column(policy.snapshot_column, table=alias)
        projections.insert(0, exp.alias_(snapshot.copy(), policy.snapshot_column))
        group_columns.insert(0, snapshot)
    projections.extend(metric_projections)

    select = exp.Select(
        expressions=projections,
        **{
            # ★ arg key 是 ``from_``（带下划线）。写 ``from`` 不会报错，
            #   sqlglot 会静默忽略未知 key，最后渲染出一条没有 FROM 的 SQL。
            #   M1 在这里踩过一次，所以下面加了结构自检。
            "from_": exp.From(this=exp.alias_(exp.to_table(table), alias)),
            "where": exp.Where(this=_combine(conditions)),
        },
    )
    if group_columns:
        select = select.group_by(*group_columns)

    if monthly_points is not None:
        select = wrap_monthly_topn(
            select,
            snapshot_column=policy.snapshot_column,
            group_columns=group_columns[1:],
            metric_alias=metric.code,
            limit=intent.top_n,
        )

    # ⑤ render 之前最后一道自检：结构必须完整且能渲染。
    # 这道检查是有来历的：FROM 曾因 arg key 写错而静默丢失，
    # 而 L4 的规则校验抓不到它（`SELECT 1` 也是合法 SQL）。
    if not select.args.get("from_"):
        raise CompileError("internal", AST_INVALID, "编译产物缺少 FROM 子句。")
    try:
        select.sql(dialect=dialect)
    except Exception as exc:  # sqlglot 的异常类型随版本变动
        raise CompileError("internal", AST_INVALID, f"编译产物无法渲染：{exc}") from exc
    return select


def compile_sql(
    intent: Intent,
    catalog: SemanticCatalog,
    *,
    scope: Scope | None = None,
    policy: ValidationPolicy | None = None,
    dialect: str = "sqlite",
    pretty: bool = True,
) -> str:
    """``compile_intent`` + 渲染成 SQL 文本。"""
    statement = compile_intent(
        intent,
        catalog,
        scope=scope,
        policy=policy,
        dialect=dialect,
    )
    return statement.sql(dialect=dialect, pretty=pretty)


# ---------------------------------------------------------------------------
# 内部步骤
# ---------------------------------------------------------------------------


def _resolve_metric(intent: Intent, catalog: SemanticCatalog) -> MetricDef:
    if not intent.metric:
        raise CompileError("intent", UNKNOWN_METRIC, "问题没有识别出要查的指标。")
    metric = catalog.metric(intent.metric)
    if metric is None:
        raise CompileError(
            "symbol_table",
            UNKNOWN_METRIC,
            f"语义层里没有指标「{intent.metric}」，无法确认口径。",
        )
    return metric


def _primary_table(
    metric: MetricDef,
    catalog: SemanticCatalog,
    scope: Scope | None,
) -> str:
    tables = catalog.metric_tables(metric)
    if not tables:
        raise CompileError(
            "symbol_table",
            UNKNOWN_SOURCE,
            f"指标「{metric.name}」没有配置来源表。",
        )

    primary = tables[0]

    # ② check（范围部分）—— 越权必须在编译期就拒绝，不能等到执行
    if scope and not scope.allows(primary):
        raise CompileError(
            "scope",
            METRIC_OUT_OF_SCOPE,
            f"指标「{metric.name}」的来源表 {primary} 不在当前可见范围内。",
        )
    return primary


def _dimension_items(
    intent: Intent,
    catalog: SemanticCatalog,
    *,
    alias: str,
) -> list[tuple[exp.Column, exp.Expression | None]]:
    """维度 -> (GROUP BY 列, 名称投影或 None)。

    ★ 名称字段永远只出现在 ``MAX()`` 里，绝不进 GROUP BY（规则 R-NAME-001）。
    这不是靠 prompt 叮嘱，而是这一段代码**写不出违规的形状**。
    """
    items: list[tuple[exp.Column, exp.Expression | None]] = []
    seen: set[str] = set()

    for name in intent.dimensions:
        dimension = catalog.dimension(name)
        if dimension is None:
            raise CompileError(
                "symbol_table",
                UNKNOWN_DIMENSION,
                f"语义层里没有维度「{name}」。",
            )
        if dimension.code in seen:
            continue
        seen.add(dimension.code)

        try:
            _level_key, level = dimension.resolve_level()
        except KeyError as exc:
            raise CompileError("intent", "DIMENSION_LEVEL_UNRESOLVED", str(exc)) from exc

        group_column = exp.column(level.group_by, table=alias)
        display_projection: exp.Expression | None = None
        if level.display and level.display.lower() != level.group_by.lower():
            display_projection = exp.alias_(
                exp.Max(this=exp.column(level.display, table=alias)),
                level.display,
            )
        items.append((group_column, display_projection))

    return items


def _combine(conditions: list[exp.Expression]) -> exp.Expression:
    """把条件列表用 AND 串成一棵树。顺序保持：时间 -> 业务 -> 权限。"""
    if not conditions:
        raise CompileError("internal", AST_INVALID, "没有生成任何过滤条件。")
    combined = conditions[0]
    for extra in conditions[1:]:
        combined = exp.And(this=combined, expression=extra)
    return combined


# ``qualify_columns`` 目前只在 metrics 内部使用；这里 re-export 便于 M2 的 JOIN 复用。
_ = qualify_columns
