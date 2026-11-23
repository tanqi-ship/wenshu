"""报表只接受完整月份；不推算自然语言，不猜缺失分区。"""

from __future__ import annotations

from calendar import monthrange

from app.engine.compiler.errors import REPORT_TIME_UNSUPPORTED, TIME_UNRESOLVED, CompileError
from app.engine.semantic.models import TimeRange


def resolve_report_months(time_range: TimeRange | None) -> tuple[str, ...]:
    if time_range is None:
        raise CompileError("intent", TIME_UNRESOLVED, "报表查询需要明确月份。")
    grain = time_range.grain
    points, start, end = time_range.points, time_range.start, time_range.end
    if grain not in {"month", "month_end", "range"}:
        raise CompileError("intent", REPORT_TIME_UNSUPPORTED, "报表只支持整月，不支持日、季度或年度粒度。")
    if points:
        if start is not None or end is not None or grain == "range":
            raise CompileError("intent", REPORT_TIME_UNSUPPORTED, "报表月份点和区间边界不能混用。")
        if grain == "month_end" and any(p.day != monthrange(p.year, p.month)[1] for p in points):
            raise CompileError("intent", REPORT_TIME_UNSUPPORTED, "month_end 时点必须确为月末。")
        return tuple(sorted({f"{p.year:04d}{p.month:02d}" for p in points}))
    if start is None or end is None:
        raise CompileError("intent", TIME_UNRESOLVED, "报表月份区间缺少完整起止边界。")
    if grain == "month_end" or start > end or start.day != 1 or end.day != monthrange(end.year, end.month)[1]:
        raise CompileError("intent", REPORT_TIME_UNSUPPORTED, "报表区间必须从月初至月末，且按时间正序。")
    # 整数月序号避免十二月/闰年及 date.max 下一月溢出。
    first = (start.year - 1) * 12 + start.month - 1
    last = (end.year - 1) * 12 + end.month - 1
    return tuple(f"{index // 12 + 1:04d}{index % 12 + 1:02d}" for index in range(first, last + 1))
