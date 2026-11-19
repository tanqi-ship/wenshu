"""业务规则校验器。

这里放的是**原本写在 5,500 字 prompt 里的规则**。

为什么搬进代码：

* 写在 prompt 里的规则，模型会"多数时候遵守"——但没有任何机制保证，
  也没有任何机制告诉你违反了哪一条。
* 写在代码里的规则，**不可能被违反**，而且每次命中都会留下一个稳定的
  problem code，可以直接拿去做失败归因。

规则编号对应《信贷问数业务规则汇编》的 C-xx，便于业务方复核。

    R-NAME-001  名称字段禁止出现在 GROUP BY             （C-11）
    R-PCT-001   百分比指标的表达式禁止乘 100             （C-07）
    R-COMP-001  多时点对比禁止用子查询查另一时点          （C-05）
    R-TOPN-001  "每月 Top N" 必须用窗口函数              （C-12）
    R-TBL-001   ETL 明细分支最多 2 张表                  （C-04）
    R-JOIN-001  报表 / UNION 分支禁止 JOIN               （C-06）
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlglot import exp

from app.engine.semantic.models import Intent
from app.engine.validation.policy import ValidationPolicy
from app.engine.validation.problems import Problem

_RANK_FUNCTIONS = frozenset({"row_number", "rank", "dense_rank", "ntile", "percent_rank", "cume_dist"})


# ---------------------------------------------------------------------------
# R-NAME-001
# ---------------------------------------------------------------------------


def name_field_in_group_by(
    statement: exp.Expression,
    policy: ValidationPolicy,
) -> list[Problem]:
    """名称字段禁止出现在 GROUP BY。

    原因：名称字段在不同机构/不同产品下可能重名，按名称分组会把同一实体
    拆成多行。做法是：GROUP BY 只用稳定的编号/编码字段，需要展示名称时用
    ``MAX(name_field)``。
    """
    suffixes = tuple(suffix.lower() for suffix in policy.name_field_suffixes)
    if not suffixes:
        return []

    problems: list[Problem] = []
    for group in statement.find_all(exp.Group):
        for projection in group.expressions:
            node = projection.this if isinstance(projection, exp.Alias) else projection
            if not isinstance(node, exp.Column):
                continue
            column = node.name
            if column and column.lower().endswith(suffixes):
                problems.append(
                    Problem(
                        code="NAME_FIELD_IN_GROUP_BY",
                        rule="C-11",
                        message=(
                            f"GROUP BY 里出现了名称字段 {column}。"
                            "请按稳定的编号/编码字段分组；需要展示名称时用 MAX(名称字段)。"
                        ),
                    )
                )
    return problems


# ---------------------------------------------------------------------------
# R-PCT-001
# ---------------------------------------------------------------------------


def percentage_metric_scaled(
    statement: exp.Expression,
    percentage_aliases: Iterable[str],
) -> list[Problem]:
    """百分比指标的表达式不得乘 100。

    约定：单位是 ``%`` 的指标返回 0~1 的小数，百分号由展示层加。
    数据库里存 0.0234、展示成 2.34% —— 一旦在 SQL 里乘了 100，
    下游任何一次再次换算都会错 100 倍，而且数字看起来"还挺像对的"。
    """
    aliases = {alias.lower() for alias in percentage_aliases if alias}
    problems: list[Problem] = []

    if aliases:
        for select in statement.find_all(exp.Select):
            for projection in select.expressions:
                alias = (projection.alias_or_name or "").lower()
                if alias in aliases and _multiplies_by(projection, 100.0):
                    problems.append(
                        Problem(
                            code="PERCENTAGE_METRIC_SCALED",
                            rule="C-07",
                            message=(
                                f"百分比指标 {projection.alias_or_name} 的表达式里出现了 * 100。"
                                "请只写 分子 / NULLIF(分母, 0)，百分号由展示层处理。"
                            ),
                        )
                    )

    # 别名没覆盖到时的兜底：乘 100 且另一侧是聚合除法 —— 大概率是百分比换算。
    # 只报 warning，因为它也可能是合法的"数量级换算"。
    for mul in statement.find_all(exp.Mul):
        if not _multiplies_by(mul, 100.0):
            continue
        other = mul.expression if _is_number(mul.this, 100.0) else mul.this
        if _contains_div_of_aggregates(other):
            alias = mul.parent.alias_or_name if mul.parent is not None else ""
            if alias and alias.lower() in aliases:
                continue  # 已经作为 error 报过了
            problems.append(
                Problem(
                    code="PERCENTAGE_SCALE_SUSPECT",
                    rule="C-07",
                    severity="warning",
                    message=("表达式里出现了「聚合除法 * 100」，如果这是百分比指标，请去掉 * 100。"),
                )
            )

    return _dedupe(problems)


# ---------------------------------------------------------------------------
# R-COMP-001
# ---------------------------------------------------------------------------


def comparison_uses_subquery(
    statement: exp.Expression,
    policy: ValidationPolicy,
) -> list[Problem]:
    """多时点对比必须一条 SELECT 一次筛出，禁止用子查询去查另一个时点。

    正确形状::

        WHERE t.etl_dt IN (DATE '2025-12-31', DATE '2024-12-31')
        ... SUM(CASE WHEN t.etl_dt = DATE '2025-12-31' THEN amt ELSE 0 END) AS cur
            SUM(CASE WHEN t.etl_dt = DATE '2024-12-31' THEN amt ELSE 0 END) AS base

    典型错误形状：外层 ``etl_dt = 当期``，再用标量子查询 ``etl_dt = 基准期``。
    这种写法在数据有缺失时静默返回 NULL 或 0，数字看上去居然还有模有样。
    """
    column = policy.snapshot_column
    if not column:
        return []

    outer = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if outer is None:
        return []

    if not _filters_snapshot(outer, column):
        return []

    offenders: list[str] = []
    for subquery in statement.find_all(exp.Subquery):
        inner = subquery.find(exp.Select)
        if inner is None or inner is outer:
            continue
        if _filters_snapshot(inner, column):
            offenders.append(subquery.sql(dialect=None)[:120])

    if not offenders:
        return []

    return [
        Problem(
            code="COMPARISON_USES_SUBQUERY",
            rule="C-05",
            message=(
                f"多时点对比不允许用子查询去查另一个 {column}。"
                f"请改为同一 SELECT 内 WHERE {column} IN (...) + "
                "SUM(CASE WHEN ... THEN ... ELSE 0 END) 分列聚合。"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# R-TOPN-001
# ---------------------------------------------------------------------------


def monthly_topn_without_window(
    statement: exp.Expression,
    intent: Intent | None,
) -> list[Problem]:
    """ "每月 Top N" 必须在 SQL 层用窗口函数实现。

    仅靠外层 ``ORDER BY ... LIMIT N`` 只能得到全局前 N，不是每月前 N。
    这类错误特别隐蔽：行数看起来是对的（都是 N 行），只是分组语义全错。
    """
    if intent is None or not intent.needs_monthly_topn:
        return []
    if _has_rank_window(statement):
        return []
    return [
        Problem(
            code="TOPN_MISSING_WINDOW",
            rule="C-12",
            message=(
                f"问题是每月 Top {intent.top_n}，但 SQL 里没有窗口排名函数。"
                "请先按「主体 + 月份」聚合，再用 ROW_NUMBER/RANK/DENSE_RANK "
                "按月份 PARTITION BY、按指标降序排名，最后在外层筛排名 <= N。"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# R-TBL-001 / R-JOIN-001
# ---------------------------------------------------------------------------


def table_count_exceeded(
    statement: exp.Expression,
    intent: Intent | None,
    policy: ValidationPolicy,
) -> list[Problem]:
    from app.engine.validation.names import count_distinct_tables

    if intent is not None and intent.source_mode != "plain":
        return []
    count = count_distinct_tables(statement)
    if count <= policy.max_tables_plain:
        return []
    return [
        Problem(
            code="TOO_MANY_TABLES",
            rule="C-04",
            message=(f"明细分支最多使用 {policy.max_tables_plain} 张表，当前用了 {count} 张。"),
        )
    ]


def join_not_allowed(
    statement: exp.Expression,
    intent: Intent | None,
    policy: ValidationPolicy,
) -> list[Problem]:
    from app.engine.validation.names import uses_join

    if intent is None or intent.source_mode not in policy.forbid_join_modes:
        return []
    if not uses_join(statement):
        return []
    return [
        Problem(
            code="JOIN_NOT_ALLOWED",
            rule="C-06",
            message=(f"{intent.source_mode} 分支不允许 JOIN。多张报表请分别查询后用 UNION ALL 合并。"),
        )
    ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _nearest_select(node: exp.Expression | None) -> exp.Select | None:
    current = node.parent if node is not None else None
    while current is not None:
        if isinstance(current, exp.Select):
            return current
        current = current.parent
    return None


def _filters_snapshot(select: exp.Select, column: str) -> bool:
    """该 SELECT 是否用等值条件限定了快照列（且不是在内层子查询里）。"""
    where = select.args.get("where")
    if where is None:
        return False
    target = column.lower()
    for equality in where.find_all(exp.EQ):
        if _nearest_select(equality) is not select:
            continue
        if _references_column(equality.this, target) or _references_column(equality.expression, target):
            return True
    return False


def _references_column(node: exp.Expression | None, target: str) -> bool:
    if node is None:
        return False
    return any(column.name and column.name.lower() == target for column in node.find_all(exp.Column))


def _is_number(node: exp.Expression | None, value: float) -> bool:
    if not isinstance(node, exp.Literal) or not node.is_number:
        return False
    try:
        return abs(float(node.this) - value) < 1e-9
    except (TypeError, ValueError):
        return False


def _multiplies_by(node: exp.Expression, value: float) -> bool:
    for mul in node.find_all(exp.Mul):
        if _is_number(mul.this, value) or _is_number(mul.expression, value):
            return True
    return False


def _contains_div_of_aggregates(node: exp.Expression | None) -> bool:
    if node is None:
        return False
    for division in node.find_all(exp.Div):
        sides = [division.this, division.expression]
        if any(
            isinstance(item, exp.AggFunc) or any(True for _ in item.find_all(exp.AggFunc))
            for item in sides
            if item is not None
        ):
            return True
    return False


def _has_rank_window(statement: exp.Expression) -> bool:
    for window in statement.find_all(exp.Window):
        inner = window.this
        name = ""
        if isinstance(inner, exp.Anonymous):
            name = str(inner.this or "").strip().lower()
        elif inner is not None:
            try:
                name = (inner.sql_name() or "").strip().lower()
            except (AttributeError, ValueError):
                name = ""
        if name in _RANK_FUNCTIONS:
            return True
    return False


def _dedupe(problems: list[Problem]) -> list[Problem]:
    seen: set[tuple[str, str]] = set()
    unique: list[Problem] = []
    for problem in problems:
        key = (problem.code, problem.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(problem)
    return unique
