"""每月 Top N：校验意图、确定月末快照，再包装聚合查询。"""

from __future__ import annotations

import calendar
from datetime import date

from sqlglot import exp

from app.engine.compiler.errors import (
    TIME_UNRESOLVED,
    TOPN_DIMENSION_REQUIRED,
    TOPN_INVALID_N,
    TOPN_OUTPUT_COLLISION,
    TOPN_TIME_UNSUPPORTED,
    TOPN_UNSUPPORTED,
    CompileError,
)
from app.engine.semantic.models import Intent


def resolve_monthly_topn(intent: Intent) -> tuple[date, ...] | None:
    """返回月末日期序列；None 表示普通查询，空元组表示区间内没有月末。"""
    if intent.top_n is None and intent.top_n_grain is None:
        return None
    if type(intent.top_n) is not int or intent.top_n <= 0:
        raise CompileError("intent", TOPN_INVALID_N, "Top N 的 N 必须是正整数。")
    if intent.top_n_grain != "month" or intent.comparison is not None or intent.source_mode != "plain":
        raise CompileError("intent", TOPN_UNSUPPORTED, "目前只支持明细分支的每月 Top N，不能与时点对比混用。")
    if not intent.dimensions:
        raise CompileError("intent", TOPN_DIMENSION_REQUIRED, "每月 Top N 必须指定排名主体（如机构或产品）。")
    time = intent.time_range
    if time is None:
        raise CompileError("intent", TIME_UNRESOLVED, "每月 Top N 缺少统计时间。")
    if time.grain not in {"month", "range", "month_end"}:
        raise CompileError("intent", TOPN_TIME_UNSUPPORTED, "每月 Top N 只支持月份、区间或月末快照。")
    if time.points:
        if time.start is not None or time.end is not None:
            raise CompileError("intent", TOPN_TIME_UNSUPPORTED, "月末时点列表与起止区间不能同时指定。")
        if any(point.day != calendar.monthrange(point.year, point.month)[1] for point in time.points):
            raise CompileError("intent", TOPN_TIME_UNSUPPORTED, "每月 Top N 的时点必须是月末快照。")
        return tuple(sorted(set(time.points)))
    if time.start is None or time.end is None:
        raise CompileError("intent", TIME_UNRESOLVED, "每月 Top N 需要月末日期列表或完整起止区间。")
    if time.start > time.end:
        raise CompileError("intent", TOPN_TIME_UNSUPPORTED, "统计时间的开始日期不能晚于结束日期。")
    # 枚举闭区间内月末，跨年不合并；缺失月份不回退、不补零。
    points: list[date] = []
    year, month = time.start.year, time.start.month
    while (year, month) <= (time.end.year, time.end.month):
        point = date(year, month, calendar.monthrange(year, month)[1])
        if time.start <= point <= time.end:
            points.append(point)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(points)


def wrap_monthly_topn(
    aggregate: exp.Select,
    *,
    snapshot_column: str,
    group_columns: list[exp.Expression],
    metric_alias: str,
    limit: int,
) -> exp.Select:
    """包装已构造的聚合查询；按月份排名，按主体编号打破并列。"""
    outputs = [projection.alias_or_name for projection in aggregate.expressions]
    lowered = [name.lower() for name in outputs]
    if len(set(lowered)) != len(lowered) or "rn" in lowered:
        raise CompileError("type_check", TOPN_OUTPUT_COLLISION, "Top N 输出列重名或使用了保留排名列 rn。")
    # 保持聚合、排名、筛选的职责独立。
    columns = [exp.column(name, table="monthly") for name in outputs]
    ranked = exp.Select(expressions=columns).from_(aggregate.subquery("monthly"))
    rank_order = [exp.Ordered(this=exp.column(metric_alias, table="monthly"), desc=True, nulls_first=False)]
    rank_order.extend(
        exp.Ordered(this=exp.column(column.name, table="monthly"), desc=False, nulls_first=True)
        for column in group_columns
    )
    ranked = ranked.select(
        exp.alias_(
            exp.Window(
                this=exp.RowNumber(),
                partition_by=[exp.column(snapshot_column, table="monthly")],
                order=exp.Order(expressions=rank_order),
                over="OVER",
            ),
            "rn",
        )
    )
    return (
        exp.Select(expressions=[exp.column(name, table="ranked") for name in outputs])
        .from_(ranked.subquery("ranked"))
        .where(exp.LTE(this=exp.column("rn", table="ranked"), expression=exp.Literal.number(limit)))
        .order_by(exp.column(snapshot_column, table="ranked"), exp.column("rn", table="ranked"))
    )
