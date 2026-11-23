"""报表月份解析不借用明细时点逻辑，必须保留完整月份。"""

from datetime import date

import pytest

from app.engine.compiler import CompileError
from app.engine.compiler.report_time import resolve_report_months
from app.engine.semantic.models import TimeRange


@pytest.mark.parametrize(
    ("time", "months"),
    [
        (
            TimeRange("month", points=(date(2026, 6, 15), date(2026, 4, 1), date(2026, 4, 30))),
            ("202604", "202606"),
        ),
        (TimeRange("month_end", points=(date(2024, 2, 29), date(2023, 2, 28))), ("202302", "202402")),
        (
            TimeRange("range", start=date(2023, 12, 1), end=date(2024, 3, 31)),
            ("202312", "202401", "202402", "202403"),
        ),
        (TimeRange("month", start=date(2026, 4, 1), end=date(2026, 6, 30)), ("202604", "202605", "202606")),
        (TimeRange("range", start=date(9999, 12, 1), end=date.max), ("999912",)),
        (TimeRange("month", points=(date.min,)), ("000101",)),
    ],
)
def test_complete_months_are_sorted_and_deduplicated(time, months) -> None:
    assert resolve_report_months(time) == months


@pytest.mark.parametrize(
    "time",
    [
        None,
        TimeRange("month"),
        TimeRange("month_end"),
        TimeRange("range"),
        TimeRange("month", start=date(2026, 4, 1)),
        TimeRange("range", end=date(2026, 4, 30)),
    ],
)
def test_missing_time_or_boundary_needs_clarification(time) -> None:
    with pytest.raises(CompileError) as error:
        resolve_report_months(time)
    assert (error.value.stage, error.value.code) == ("intent", "TIME_UNRESOLVED")


@pytest.mark.parametrize(
    "time",
    [
        TimeRange("day", points=(date(2026, 4, 30),)),
        TimeRange("quarter"),
        TimeRange("year"),
        TimeRange("month_end", points=(date(2024, 2, 28),)),
        TimeRange("month_end", points=(date(2026, 4, 1),)),
        TimeRange("range", points=(date(2026, 4, 30),)),
        TimeRange("month", points=(date(2026, 4, 1),), start=date(2026, 4, 1)),
        TimeRange("month", points=(date(2026, 4, 1),), end=date(2026, 4, 30)),
        TimeRange("range", start=date(2026, 4, 15), end=date(2026, 6, 30)),
        TimeRange("range", start=date(2026, 4, 1), end=date(2026, 6, 15)),
        TimeRange("range", start=date(2026, 6, 1), end=date(2026, 4, 30)),
        TimeRange("month_end", start=date(2026, 4, 1), end=date(2026, 4, 30)),
    ],
)
def test_partial_or_mixed_time_shapes_fail(time) -> None:
    with pytest.raises(CompileError) as error:
        resolve_report_months(time)
    assert (error.value.stage, error.value.code) == ("intent", "REPORT_TIME_UNSUPPORTED")
