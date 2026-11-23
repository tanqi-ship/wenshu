"""时间条件编译。

这一层是「规则变代码」最划算的一处。askdb 把这套规则写在 prompt 里：

    "月份命中 ETL_DT 时使用月末时点"
    "两个或三个时点的对比必须在同一个 SELECT 中用 WHERE etl_dt IN (...) 一次筛选"

两条都不可单测、不可回归，而且互相干扰。这里它们是两个分支。

**编译器不解析自然语言时间。** ``TimeRange.points`` / ``start`` / ``end``
必须由 L1/L2 填好；没填就报 ``TIME_UNRESOLVED`` → 回 L2 澄清。
让编译器去猜"2026年4月末"是几号，就是把不确定性又请回来了。
"""

from __future__ import annotations

import calendar
from datetime import date

from sqlglot import exp

from app.engine.compiler.errors import TIME_UNRESOLVED, TIME_UNSUPPORTED, CompileError
from app.engine.semantic.models import TimeRange
from app.engine.validation.policy import ValidationPolicy


def month_end_range(year: int, month: int) -> TimeRange:
    """构造一个"某月月末快照"的时间条件。

    月末由 :mod:`calendar` 算，不由模型猜 —— 2 月是 28 还是 29 号、
    4 月是 30 号，这类事代码永远比模型可靠。
    """
    last_day = calendar.monthrange(year, month)[1]
    return TimeRange(
        grain="month_end",
        raw=f"{year:04d}-{month:02d}",
        points=(date(year, month, last_day),),
    )


def build_time_condition(
    time_range: TimeRange | None,
    *,
    alias: str,
    policy: ValidationPolicy | None = None,
    dialect: str = "sqlite",
) -> exp.Expression:
    """把 ``TimeRange`` 编译成一个 WHERE 条件。"""
    policy = policy or ValidationPolicy()
    if time_range is None:
        raise CompileError("intent", TIME_UNRESOLVED, "问题没有时间条件，无法确定统计时点。")

    column = exp.column(policy.snapshot_column, table=alias)
    grain = time_range.grain

    if grain in {"month_end", "day"}:
        points = tuple(time_range.points)
        if not points and time_range.start is not None:
            points = (time_range.start,)
        if not points:
            raise CompileError(
                "intent",
                TIME_UNRESOLVED,
                f"时间条件（{time_range.raw or grain}）没有解析出具体时点，需要先澄清。",
            )
        return build_point_condition(column, points, dialect=dialect)

    if grain == "range":
        if time_range.start is None or time_range.end is None:
            raise CompileError(
                "intent",
                TIME_UNRESOLVED,
                f"时间区间（{time_range.raw or grain}）缺少起止日期，需要先澄清。",
            )
        return exp.Between(
            this=column,
            low=exp.Literal.string(time_range.start.isoformat()),
            high=exp.Literal.string(time_range.end.isoformat()),
        )

    # 普通 month / quarter / year 聚合尚未支持；每月 Top N 由 topn.py 单独处理。
    raise CompileError(
        "intent",
        TIME_UNSUPPORTED,
        f"时间粒度 {grain} 尚未支持（M2 实现：需要保留月份维度的 GROUP BY）。",
    )


def build_point_condition(
    column: exp.Expression,
    points: tuple[date, ...],
    *,
    dialect: str,
) -> exp.Expression:
    """单时点用 ``=``，多时点用 ``IN``。

    对比查询用 IN 一次筛出时点（R-COMP-001），不使用标量子查询分别查；
    每月 Top N 同样复用 IN 筛选月末快照，后续按月分组、排名。
    """
    del dialect  # 方言差异（DATE '...' vs 字符串）留到 M2；SQLite 用字符串
    literals = [exp.Literal.string(point.isoformat()) for point in points]
    if len(literals) == 1:
        return exp.EQ(this=column, expression=literals[0])
    return exp.In(this=column, expressions=literals)
