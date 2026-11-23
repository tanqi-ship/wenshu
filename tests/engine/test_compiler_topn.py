"""M2.2：每月 Top N 的结构、实际结果和拒绝边界。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, compile_sql
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Filter, Intent, MetricDef, TimeRange, intent_from_mapping
from app.engine.validation import ValidationPolicy, validate_sql


def monthly_intent(**changes: object) -> Intent:
    return replace(
        Intent(
            metric="贷款余额",
            time_range=TimeRange(grain="month", start=date(2026, 1, 1), end=date(2026, 4, 30)),
            dimensions=("机构",),
            top_n=1,
            top_n_grain="month",
        ),
        **changes,
    )


def test_monthly_topn_has_separate_aggregation_ranking_and_filter() -> None:
    statement = compile_intent(monthly_intent(), semantic_catalog())
    assert len(list(statement.find_all(exp.Select))) == 3
    assert statement.find(exp.Limit) is None
    assert [p.alias_or_name for p in statement.expressions] == [
        "etl_dt",
        "org_no",
        "org_nm",
        "loan_balance",
    ]
    window = statement.find(exp.Window)
    assert isinstance(window.this, exp.RowNumber)
    assert [c.name for c in window.args["partition_by"]] == ["etl_dt"]
    ordered = window.args["order"].expressions
    assert [(item.this.name, bool(item.args.get("desc"))) for item in ordered] == [
        ("loan_balance", True),
        ("org_no", False),
    ]
    aggregate = next(s for s in statement.find_all(exp.Select) if s.args.get("group"))
    assert [c.name for c in aggregate.args["group"].expressions] == ["etl_dt", "org_no"]
    assert isinstance(aggregate.expressions[2].this, exp.Max)
    assert statement.args["where"].this.left.name == "rn"
    assert statement.args["where"].this.right.this == "1"
    result = validate_sql(
        statement.sql(dialect="sqlite"),
        catalog=validation_catalog(),
        intent=monthly_intent(),
        dialect="sqlite",
    )
    assert result.ok, result.codes


@pytest.fixture
def snapshots():
    """数据特意含并列、同主体多笔贷款、名称漂移、月中快照和缺失月份。"""
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            f"CREATE TABLE {TABLE} (etl_dt TEXT, org_no TEXT, org_nm TEXT, current_balnc_cny REAL, prd_no TEXT, prd_nm TEXT, fiv_lev_cla_flag TEXT)"
        )
        connection.executemany(
            f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-01-31", "B001", "支行甲", 60, "LP01", "产品甲", "30"),
                ("2026-01-31", "B001", "支行甲 ", 40, "LP01", "产品甲", "10"),
                ("2026-01-31", "B002", "支行乙", 100, "LP03", "产品乙", "30"),
                ("2026-01-31", "B003", "支行丙", 90, "LP01", "产品甲", "10"),
                ("2026-02-28", "B001", "支行甲", 10, "LP01", "产品甲", "10"),
                ("2026-02-28", "B002", "支行乙", 120, "LP03", "产品乙", "30"),
                ("2026-02-28", "B003", "支行丙", 80, "LP01", "产品甲", "10"),
                ("2026-02-15", "B003", "支行丙", 100000, "LP01", "产品甲", "30"),
                ("2026-04-30", "B001", "支行甲", 30, "LP01", "产品甲", "10"),
                ("2026-04-30", "B002", "支行乙", 20, "LP03", "产品乙", "30"),
                ("2026-04-30", "B003", "支行丙", 50, "LP01", "产品甲", "30"),
                ("2027-01-31", "B003", "支行丙", 200, "LP01", "产品甲", "30"),
            ],
        )
        yield connection


def execute(connection, intent: Intent, *, scope: Scope | None = None):
    sql = compile_sql(intent, semantic_catalog(), scope=scope)
    rows = connection.execute(sql).fetchall()
    return [(row[0], row[1], row[-1]) for row in rows]


def test_results_rank_each_month_and_skip_missing_snapshots(snapshots) -> None:
    assert execute(snapshots, monthly_intent()) == [
        ("2026-01-31", "B001", 100),
        ("2026-02-28", "B002", 120),
        ("2026-04-30", "B003", 50),
    ]


def test_top_two_returns_two_per_month_with_stable_ties(snapshots) -> None:
    assert execute(snapshots, monthly_intent(top_n=2)) == [
        ("2026-01-31", "B001", 100),
        ("2026-01-31", "B002", 100),
        ("2026-02-28", "B002", 120),
        ("2026-02-28", "B003", 80),
        ("2026-04-30", "B003", 50),
        ("2026-04-30", "B001", 30),
    ]


def test_scope_filters_before_ranking(snapshots) -> None:
    scope = Scope.of(
        [TABLE],
        row_policies=(RowPolicy(table=TABLE, column="org_no", values=("B001", "B003"), operator="in"),),
    )
    assert execute(snapshots, monthly_intent(), scope=scope) == [
        ("2026-01-31", "B001", 100),
        ("2026-02-28", "B003", 80),
        ("2026-04-30", "B003", 50),
    ]
    closed = Scope.of([TABLE], row_policies=(RowPolicy(table=TABLE, column="org_no"),))
    assert execute(snapshots, monthly_intent(), scope=closed) == []


def test_business_filter_stays_inside_aggregation(snapshots) -> None:
    intent = monthly_intent(filters=(Filter(dimension="机构", value="渝北支行"),))
    assert execute(snapshots, intent) == [
        ("2026-01-31", "B001", 100),
        ("2026-02-28", "B001", 10),
        ("2026-04-30", "B001", 30),
    ]


def test_ranking_works_for_product_and_conditional_metric(snapshots) -> None:
    assert execute(snapshots, monthly_intent(dimensions=("产品",), metric="不良贷款余额")) == [
        ("2026-01-31", "LP03", 100),
        ("2026-02-28", "LP03", 120),
        ("2026-04-30", "LP01", 50),
    ]


def test_month_partitions_keep_the_year(snapshots) -> None:
    time = TimeRange(grain="month", points=(date(2027, 1, 31), date(2026, 1, 31)))
    assert execute(snapshots, monthly_intent(time_range=time)) == [
        ("2026-01-31", "B001", 100),
        ("2027-01-31", "B003", 200),
    ]


def test_interval_without_month_end_returns_no_rows(snapshots) -> None:
    time = TimeRange(grain="range", start=date(2026, 2, 1), end=date(2026, 2, 20))
    assert execute(snapshots, monthly_intent(time_range=time)) == []


def test_n_larger_than_population_returns_all_available_subjects(snapshots) -> None:
    assert len(execute(snapshots, monthly_intent(top_n=10))) == 9


def test_explicit_points_are_sorted_deduplicated_and_not_treated_as_comparison() -> None:
    time = TimeRange(grain="month_end", points=(date(2026, 4, 30), date(2026, 1, 31), date(2026, 1, 31)))
    statement = compile_intent(monthly_intent(time_range=time), semantic_catalog())
    assert [p.this for p in statement.find(exp.In).expressions] == ["2026-01-31", "2026-04-30"]


def test_custom_snapshot_column_and_table_alias() -> None:
    statement = compile_intent(
        monthly_intent(),
        semantic_catalog(),
        alias="loans",
        policy=ValidationPolicy(snapshot_column="snapshot_dt"),
    )
    assert "snapshot_dt" in [p.alias_or_name for p in statement.expressions]
    assert statement.find(exp.Window).args["partition_by"][0].name == "snapshot_dt"
    assert statement.find(exp.In).this.table == "loans"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"top_n": 0}, "TOPN_INVALID_N"),
        ({"top_n": -1}, "TOPN_INVALID_N"),
        ({"top_n": True}, "TOPN_INVALID_N"),
        ({"top_n": 1.5}, "TOPN_INVALID_N"),
        ({"top_n": "2"}, "TOPN_INVALID_N"),
        ({"top_n": None}, "TOPN_INVALID_N"),
        ({"top_n_grain": None}, "TOPN_UNSUPPORTED"),
        ({"top_n_grain": "year"}, "TOPN_UNSUPPORTED"),
        ({"comparison": "delta"}, "TOPN_UNSUPPORTED"),
        ({"source_mode": "report"}, "TOPN_UNSUPPORTED"),
        ({"dimensions": ()}, "TOPN_DIMENSION_REQUIRED"),
        ({"time_range": None}, "TIME_UNRESOLVED"),
        ({"time_range": TimeRange(grain="month")}, "TIME_UNRESOLVED"),
        ({"time_range": TimeRange(grain="month", start=date(2026, 1, 1))}, "TIME_UNRESOLVED"),
        ({"time_range": TimeRange(grain="day", points=(date(2026, 1, 31),))}, "TOPN_TIME_UNSUPPORTED"),
        ({"time_range": TimeRange(grain="month", points=(date(2026, 1, 15),))}, "TOPN_TIME_UNSUPPORTED"),
        (
            {"time_range": TimeRange(grain="range", start=date(2026, 4, 30), end=date(2026, 1, 1))},
            "TOPN_TIME_UNSUPPORTED",
        ),
        (
            {"time_range": TimeRange(grain="month", points=(date(2026, 1, 31),), start=date(2026, 1, 1))},
            "TOPN_TIME_UNSUPPORTED",
        ),
    ],
)
def test_unsupported_inputs_raise_explicit_intent_error(changes, code: str) -> None:
    with pytest.raises(CompileError) as error:
        compile_intent(monthly_intent(**changes), semantic_catalog())
    assert (error.value.stage, error.value.code) == ("intent", code)


@pytest.mark.parametrize("value", [True, False, 1.5, 0, -1, "1.5", "abc"])
def test_loader_does_not_silently_coerce_invalid_n(value) -> None:
    with pytest.raises(ValueError, match="top_n"):
        intent_from_mapping({"top_n": value})


def test_loader_keeps_integer_string_compatibility() -> None:
    assert intent_from_mapping({"top_n": "3"}).top_n == 3


def test_reserved_or_duplicate_output_name_is_rejected() -> None:
    for name in ("rn", "org_no", "etl_dt"):
        base = semantic_catalog()
        catalog = SemanticCatalog.build(
            metrics=[
                MetricDef(
                    code=name, name="贷款余额", formula="SUM(current_balnc_cny)", source_tables=(TABLE,)
                )
            ],
            dimensions=base.dimensions.values(),
        )
        with pytest.raises(CompileError) as error:
            compile_intent(monthly_intent(), catalog)
        assert (error.value.stage, error.value.code) == ("type_check", "TOPN_OUTPUT_COLLISION")


def test_leap_year_and_partial_interval_select_only_included_month_ends() -> None:
    time = TimeRange(grain="month", start=date(2024, 2, 15), end=date(2024, 4, 15))
    statement = compile_intent(monthly_intent(time_range=time), semantic_catalog())
    assert [p.this for p in statement.find(exp.In).expressions] == ["2024-02-29", "2024-03-31"]


def test_multiple_dimensions_rank_the_combination_with_all_codes_as_tiebreakers() -> None:
    statement = compile_intent(monthly_intent(dimensions=("机构", "产品")), semantic_catalog())
    group = statement.find(exp.Group)
    assert [c.name for c in group.expressions] == ["etl_dt", "org_no", "prd_no"]
    order = statement.find(exp.Window).args["order"].expressions
    assert [item.this.name for item in order] == ["loan_balance", "org_no", "prd_no"]


def test_null_ratio_ranks_after_defined_values(snapshots) -> None:
    snapshots.execute(f"UPDATE {TABLE} SET current_balnc_cny = 0 WHERE org_no = 'B001'")
    intent = monthly_intent(
        metric="不良贷款率", top_n=3, time_range=TimeRange(grain="month", points=(date(2026, 1, 31),))
    )
    assert execute(snapshots, intent) == [
        ("2026-01-31", "B002", 1),
        ("2026-01-31", "B003", 0),
        ("2026-01-31", "B001", None),
    ]
