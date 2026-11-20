"""多时点对比的投影构造（M2.1：只做 ``delta``）。

产出形状（决策：**重复完整表达式**，不用 CTE、不用子查询）::

    SELECT SUM(CASE WHEN t1.etl_dt = '<cur>'  THEN <聚合参数> ELSE 0 END) AS cur_balance,
           SUM(CASE WHEN t1.etl_dt = '<base>' THEN <聚合参数> ELSE 0 END) AS base_balance,
           SUM(CASE WHEN t1.etl_dt = '<cur>'  THEN <聚合参数> ELSE 0 END)
         - SUM(CASE WHEN t1.etl_dt = '<base>' THEN <聚合参数> ELSE 0 END) AS delta
    FROM <来源表> AS t1
    WHERE t1.etl_dt IN ('<cur>', '<base>')

## 为什么差值要重复写两遍

SQL **不允许同一个 SELECT 里引用本层别名**：``cur_balance - base_balance``
在任何数据库都不合法（别名是给 ``ORDER BY`` 和外层查询用的）。三条出路：

============ ================================================== ==================================
方案          形状                                               代价
============ ================================================== ==================================
(a) 重复表达式 ``SUM(CASE…) - SUM(CASE…)``                          SQL 长约 3 倍（选这个）
(b) CTE        ``WITH t AS (SELECT …cur, …base) SELECT cur-base``  要先把"CTE 算不算子查询"定义清楚
(c) 子查询      嵌套 SELECT                                        被 R-COMP-001 直接排除
============ ================================================== ==================================

选 (a)：**代价是长度，换来的是 `R-COMP-001` 的边界清晰**。
可读性可以用视图补，而"对比查询里藏了一个子查询"这种事，只有在 review
黄金 SQL 时才看得出来 —— 所以宁可让它长。

## 为什么只接受"可加总"指标

时点条件是注入到**聚合函数的参数**里的，不是套在聚合结果外面。
对 ``SUM`` 这是恒等式（分段求和再相加 = 总体求和）：

    SUM(x) = 0 + SUM(x | 时点 A) + SUM(x | 时点 B)

对 ``AVG`` / ``COUNT`` / 比率就不成立（分段平均再相减没有意义）。
所以指标公式的**顶层必须是单个 ``SUM(...)``**，否则明确报
``DELTA_NOT_ADDITIVE``，而不是产出一条跑得通、口径却错的 SQL。

这条边界是 M2.1 的显式取舍，不是能力不足：``npl_ratio``（不良率）做对比
本来也不该做减法（"-2 个百分点"需要先定清楚是 pp 还是相对变化）。
"""

from __future__ import annotations

from datetime import date

from sqlglot import exp

from app.engine.compiler.errors import (
    AST_INVALID,
    COMPARISON_POINT_COUNT,
    COMPARISON_UNSUPPORTED,
    DELTA_NOT_ADDITIVE,
    MULTI_POINT_WITHOUT_COMPARISON,
    CompileError,
)
from app.engine.compiler.metrics import parse_metric_formula
from app.engine.semantic.models import Comparison, Intent, MetricDef

__all__ = [
    "BASE_BALANCE_ALIAS",
    "CUR_BALANCE_ALIAS",
    "DELTA_ALIAS",
    "build_metric_projections",
    "resolve_comparison_points",
]

#: 三列的别名。这是 ``eval/cases/asset_quality.yaml`` 里 AQ-004 已经定下的输出约定
#: （"排序和展示需要当前值、基准值、差值三列，不能只返回差值"）。
CUR_BALANCE_ALIAS = "cur_balance"
BASE_BALANCE_ALIAS = "base_balance"
DELTA_ALIAS = "delta"

#: M2.1 支持且仅支持 delta。yoy / mom / rate 的形状不同（跨年、跨月、除法），
#: 遇到时**明确报错**，绝不静默按 delta 处理。
_SUPPORTED_COMPARISONS: frozenset[str] = frozenset({"delta"})

#: 能用差值的时点粒度。``range`` 不行：那是闭区间，没有"当前/基准"两个点。
_POINT_GRAINS: frozenset[str] = frozenset({"month_end", "day"})


def resolve_comparison_points(intent: Intent) -> tuple[date, ...]:
    """校验多时点意图，返回**已校验的时点元组**（0 / 1 / 2 个）。

    返回值的两个用途：``len()`` 决定投影形状，``[0]``/``[1]`` 就是当前/基准。
    非对比意图原样返回 ``time_range.points``（可能是 0 个；区间形态就是这样）。

    四条校验都是"明确报错"，理由统一：**多时点一旦被当单时点处理，SQL 会变成
    "把两个时点加在一起"** —— 跑得通、不报错、数字还挺像，是最难发现的一类错。
    """
    time_range = intent.time_range
    points = tuple(time_range.points) if time_range is not None else ()
    comparison = intent.comparison

    if comparison is None:
        if len(points) > 1:
            raise CompileError(
                "intent",
                MULTI_POINT_WITHOUT_COMPARISON,
                f"时间条件里有 {len(points)} 个时点，但没说怎么比（comparison 为空）。"
                "请明确是增加额（delta）还是别的比较方式；否则只能按单时点澄清。",
            )
        return points

    if comparison not in _SUPPORTED_COMPARISONS:
        raise CompileError(
            "intent",
            COMPARISON_UNSUPPORTED,
            f"比较方式「{comparison}」尚未支持：M2.1 只实现 delta（两个时点的差值）。"
            "同比 / 环比 / 比率需要不同的 SQL 形状，留到 M2 后续实现。",
        )

    if time_range is None or time_range.grain not in _POINT_GRAINS:
        grain = time_range.grain if time_range is not None else "（无时间条件）"
        raise CompileError(
            "intent",
            COMPARISON_UNSUPPORTED,
            f"delta 只能基于时点（month_end / day），当前时间粒度是 {grain}。",
        )

    if len(points) != 2:
        raise CompileError(
            "intent",
            COMPARISON_POINT_COUNT,
            f"delta 需要恰好两个时点（顺序：当前、基准），实际拿到 {len(points)} 个。",
        )

    return points


def build_metric_projections(
    metric: MetricDef,
    *,
    points: tuple[date, ...],
    comparison: Comparison | None,
    alias: str,
    snapshot_column: str,
    dialect: str = "sqlite",
) -> list[exp.Expression]:
    """产出指标投影。

    * 单时点 / 无时点（区间）→ 一列：``SUM(...) AS <metric.code>``（M1 的原有形状）
    * 双时点 delta       → 三列：``cur_balance`` / ``base_balance`` / ``delta``

    接收的是**已解析的指标定义**和**已校验的时点**，不接收 ``Intent`` ——
    这样"哪些形状合法"的判断留在 :func:`resolve_comparison_points` 一处，
    这里只负责造节点。
    """
    metric_expr = parse_metric_formula(metric, alias=alias, dialect=dialect)

    if len(points) != 2:
        # M1 原有形状：时间条件由 time.py 负责（单点 =、区间 BETWEEN），
        # 投影就是指标公式本身，不需要 CASE WHEN。
        return [exp.alias_(metric_expr, metric.code)]

    if comparison != "delta":
        # 防御性检查：两个时点必须是 delta（resolve_comparison_points 已经挡住其它情况）。
        # 放这里是因为"两个时点 ⇒ delta"不该是一个隐含约定。
        raise CompileError(
            "internal",
            AST_INVALID,
            f"指标 {metric.code} 拿到两个时点，但比较方式不是 delta（{comparison!r}）。",
        )

    if not isinstance(metric_expr, exp.Sum):
        raise CompileError(
            "type_check",
            DELTA_NOT_ADDITIVE,
            f"指标「{metric.name}」的公式顶层不是 SUM，无法做差值对比。"
            "M2.1 只支持可加总指标（余额、金额这类）。",
        )

    current = _point_sum(metric_expr, points[0], alias=alias, snapshot_column=snapshot_column)
    base = _point_sum(metric_expr, points[1], alias=alias, snapshot_column=snapshot_column)

    # 用 copy 而不是复用同一个节点：exp.alias_ 会给节点挂上 alias，
    # 同一棵子树既当独立投影又当差值的一部分，必须互不影响。
    delta = exp.Sub(this=current.copy(), expression=base.copy())
    return [
        exp.alias_(current, CUR_BALANCE_ALIAS),
        exp.alias_(base, BASE_BALANCE_ALIAS),
        exp.alias_(delta, DELTA_ALIAS),
    ]


def _point_sum(
    metric_expr: exp.Sum,
    point: date,
    *,
    alias: str,
    snapshot_column: str,
) -> exp.Sum:
    """把时点条件注入聚合**参数**：``SUM(x)`` → ``SUM(CASE WHEN etl_dt = '<p>' THEN x ELSE 0 END)``。

    ``ELSE 0`` 的作用是让缺失时点不影响其它时点。**注意这不等于确认了
    "缺失快照就是零"的业务语义**（见 ``eval/golden/G-008.sql`` 的口径提醒）：
    它在数学上是"分段求和"的正确写法，业务上是"没有数据就当 0"，两者要分开谈。
    """
    point_condition = exp.EQ(
        this=exp.column(snapshot_column, table=alias),
        expression=exp.Literal.string(point.isoformat()),
    )
    guarded = exp.Case(
        ifs=[exp.If(this=point_condition, true=metric_expr.this.copy())],
        default=exp.Literal.number(0),
    )
    return exp.Sum(this=guarded)
