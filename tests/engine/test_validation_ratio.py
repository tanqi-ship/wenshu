"""独立手写 SQL 的正反例，不用 L3 产生校验器的错误标准答案。"""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, validation_catalog
from app.engine.semantic.models import Filter, Intent, ShareSpec, TimeRange
from app.engine.validation import validate_sql

DIRECTORY = Path(__file__).resolve().parents[2] / "eval" / "golden"
SQL = (DIRECTORY / "G-020.sql").read_text(encoding="utf-8")


def intent():
    from datetime import date

    return Intent(
        metric="贷款余额",
        time_range=TimeRange("month_end", points=(date(2026, 4, 30),)),
        dimensions=("产品",),
        share=ShareSpec("产品"),
        filters=(Filter("机构", "B001"), Filter("产品", "LP03")),
    )


def check(sql=SQL, **kwargs):
    return validate_sql(sql, catalog=validation_catalog(), intent=kwargs.pop("intent", intent()), **kwargs)


def test_valid_independent_sql_passes():
    assert check().ok, check().feedback()


@pytest.mark.parametrize(
    "replacement",
    [
        "SUM(share_groups.loan_balance) OVER (PARTITION BY share_groups.prd_no)",
        "SUM(share_groups.loan_balance) OVER (ORDER BY share_groups.prd_no)",
        "SUM(share_groups.loan_balance) OVER (ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "AVG(share_groups.loan_balance) OVER ()",
        "SUM(share_groups.loan_balance * 0) OVER ()",
        "SUM(share_groups.prd_no) OVER ()",
        "share_groups.loan_balance",
    ],
)
def test_denominator_is_not_partition_cumulative_other_metric_or_presence_of_any_window(replacement):
    result = check(SQL.replace("SUM(share_groups.loan_balance) OVER ()", replacement))
    assert "RATIO_SHAPE_INVALID" in result.codes


@pytest.mark.parametrize(
    "replacement",
    [
        "share_totals.loan_balance / NULLIF(share_totals.total_value, 0)",
        "1 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0)",
        "100.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0)",
        "1.0 * share_totals.loan_balance / share_totals.total_value",
        "1.0 * share_totals.loan_balance / NULLIF(share_totals.loan_balance, 0)",
        "1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 1)",
        "1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 1e-1000)",
        "COALESCE(1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0), 0)",
        "ROUND(1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0), 2)",
        "CAST(share_totals.loan_balance AS INTEGER) / NULLIF(share_totals.total_value, 0)",
        "CAST(share_totals.loan_balance AS DECIMAL) / NULLIF(share_totals.total_value, 0)",
        "CAST(share_totals.loan_balance AS DECIMAL(10, 2)) / NULLIF(share_totals.total_value, 0)",
    ],
)
def test_ratio_guard_scaling_and_integer_division(replacement):
    original = "1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0)"
    result = check(SQL.replace(original, replacement))
    assert "RATIO_SHAPE_INVALID" in result.codes


def test_explicit_real_cast_can_safely_promote_integer_numerator():
    sql = SQL.replace("1.0 * share_totals.loan_balance", "CAST(share_totals.loan_balance AS REAL)")
    assert check(sql).ok, check(sql).feedback()


def test_moving_display_filter_into_base_is_a_real_denominator_error():
    sql = SQL.replace("WHERE share_totals.prd_no = 'LP03'\n", "").replace(
        "AND t1.org_no = 'B001'", "AND t1.org_no = 'B001' AND t1.prd_no = 'LP03'"
    )
    result = check(sql)
    assert "RATIO_DENOMINATOR_FILTERED" in result.codes
    assert any(p.rule == "R-RATIO-001" for p in result.problems)


@pytest.mark.parametrize(
    "operator,values,predicate",
    [
        ("eq", ("LP03",), "t1.prd_no = 'LP03'"),
        ("in", ("LP03",), "t1.prd_no IN ('LP03')"),
        ("eq", ("LP01", "LP03"), "t1.prd_no IN ('LP01', 'LP03')"),
        ("in", (), "1 = 0"),
        ("eq", ("",), "1 = 0"),
    ],
)
def test_group_column_row_policies_are_required_inside_not_confused_with_display(operator, values, predicate):
    scope = Scope.of([TABLE], row_policies=(RowPolicy(TABLE, "prd_no", values=values, operator=operator),))
    sql = SQL.replace("AND t1.org_no = 'B001'", f"AND t1.org_no = 'B001' AND {predicate}")
    result = check(sql, scope=scope)
    assert result.ok, result.feedback()
    assert "RATIO_SCOPE_POLICY_MISSING" in check(SQL, scope=scope).codes
    if any(str(value) for value in values):
        assert "RATIO_DENOMINATOR_FILTERED" in check(sql).codes


def test_empty_policy_moved_to_outer_is_not_equivalent_for_security_contract():
    scope = Scope.of([TABLE], row_policies=(RowPolicy(TABLE, "prd_no"),))
    sql = SQL.replace("WHERE share_totals.prd_no = 'LP03'", "WHERE share_totals.prd_no = 'LP03' AND 1 = 0")
    assert "RATIO_SCOPE_POLICY_MISSING" in check(sql, scope=scope).codes


def test_policy_under_or_does_not_count_as_retained():
    scope = Scope.of([TABLE], row_policies=(RowPolicy(TABLE, "prd_no", values=("LP01", "LP03")),))
    sql = SQL.replace(
        "AND t1.org_no = 'B001'", "AND t1.org_no = 'B001' AND (t1.prd_no IN ('LP01', 'LP03') OR 1=1)"
    )
    assert not check(sql, scope=scope).ok


@pytest.mark.parametrize(
    "old,new",
    [
        ("'2026-04-30'", "'2026-03-31'"),
        ("WHERE t1.etl_dt = '2026-04-30' AND t1.org_no = 'B001'", "WHERE t1.org_no = 'B001'"),
        ("SUM(t1.current_balnc_cny)", "AVG(t1.current_balnc_cny)"),
        ("SUM(t1.current_balnc_cny)", "SUM(DISTINCT t1.current_balnc_cny)"),
        ("SUM(t1.current_balnc_cny)", "SUM(SUM(t1.current_balnc_cny))"),
        ("GROUP BY t1.prd_no", "GROUP BY t1.prd_no, t1.org_no"),
        ("GROUP BY t1.prd_no", "GROUP BY t1.prd_no HAVING SUM(t1.current_balnc_cny) > 100"),
        ("GROUP BY t1.prd_no", "GROUP BY t1.prd_no LIMIT 1"),
        (") AS share_groups", ") AS share_groups WHERE share_groups.prd_no = 'LP03'"),
        (") AS share_groups", ") AS share_groups LIMIT 1"),
        ("WHERE share_totals.prd_no = 'LP03'", "WHERE share_totals.total_value > 100"),
        ("ORDER BY share_totals.prd_no", "ORDER BY share_totals.prd_no DESC"),
        ("ORDER BY share_totals.prd_no", "ORDER BY share_totals.loan_balance"),
        ("SELECT\n  share_totals.prd_no", "SELECT DISTINCT\n  share_totals.prd_no"),
        (
            "FROM qa_loan_acc_detail AS t1",
            "FROM qa_loan_acc_detail AS t1 JOIN dim_org AS d ON t1.org_no = d.org_no",
        ),
        ("WHERE t1.etl_dt = '2026-04-30'", "WHERE t1.etl_dt = '2026-04-30' AND t1.etl_dt = '2026-03-31'"),
        ("t1.prd_no AS prd_no", "t1.prd_no + 1 AS prd_no"),
        ("share_totals.loan_balance,", "share_totals.loan_balance * 0,"),
    ],
)
def test_tampered_shape_time_and_dataflow_rejected(old, new):
    assert old in SQL
    result = check(SQL.replace(old, new))
    assert "RATIO_SHAPE_INVALID" in result.codes, result.feedback()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail",
        "SELECT * FROM qa_loan_acc_detail",
        "SELECT 1 UNION ALL SELECT 2",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT 1 FROM (SELECT 1 FROM (SELECT 1) AS x) AS y",
        "SELECT 1 FROM (SELECT 1 FROM qa_loan_acc_detail) AS y",
    ],
)
def test_malformed_but_parseable_sql_returns_problem_not_exception(sql):
    assert "RATIO_SHAPE_INVALID" in check(sql).codes


def test_rule_only_applies_when_share_intent_is_present():
    assert check("SELECT 1", intent=None).ok
    assert check("SELECT 1", intent=replace(intent(), share=None)).ok
    assert "RATIO_SHAPE_INVALID" in check(intent=replace(intent(), share=True)).codes


def test_renaming_internal_aliases_does_not_disable_rule():
    sql = SQL.replace("share_groups", "groups_x").replace("share_totals", "totals_x")
    assert check(sql).ok, check(sql).feedback()
    bad = sql.replace("OVER ()", "OVER (PARTITION BY groups_x.prd_no)")
    assert "RATIO_SHAPE_INVALID" in check(bad).codes


def test_validation_does_not_import_compiler_or_semantic_catalog():
    path = Path(__file__).resolve().parents[2] / "app" / "engine" / "validation" / "ratio.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert all(not name.startswith("app.engine.compiler") for name in imports)
    assert "app.engine.semantic.catalog" not in imports
