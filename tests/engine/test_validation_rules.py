"""业务规则校验器（C-04 / C-05 / C-06 / C-07 / C-11 / C-12）。

这些规则原本写在 askdb 节点 19 的 5,502 字 prompt 里。搬进代码后，
每一条都能被单测覆盖，而且违反时不可能被静默忽略。
"""

from __future__ import annotations

import pytest

from app.engine.semantic.models import Intent, TimeRange
from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import ValidationPolicy
from tests.constants import DIALECT


def _validate(sql: str, catalog: Catalog, **kwargs):
    return validate_sql(sql, catalog=catalog, dialect=DIALECT, **kwargs)


class TestNameFieldInGroupBy:
    """R-NAME-001 / C-11：名称字段禁止出现在 GROUP BY。"""

    def test_group_by_name_field_is_rejected(self, catalog: Catalog) -> None:
        result = _validate(
            "SELECT prd_nm, SUM(current_balnc_cny) FROM qa_loan_acc_detail GROUP BY prd_nm",
            catalog,
        )
        assert not result.ok
        assert "NAME_FIELD_IN_GROUP_BY" in result.codes

    def test_group_by_code_field_passes(self, catalog: Catalog) -> None:
        result = _validate(
            "SELECT prd_no, MAX(prd_nm) AS prd_nm, SUM(current_balnc_cny) "
            "FROM qa_loan_acc_detail GROUP BY prd_no",
            catalog,
        )
        assert result.ok, result.feedback()

    def test_max_of_name_field_is_allowed(self, catalog: Catalog) -> None:
        """需求是"展示名称"，做法是 MAX(名称)，不能因此报错。"""
        result = _validate(
            "SELECT prd_no, MAX(prd_nm) AS nm, SUM(current_balnc_cny) "
            "FROM qa_loan_acc_detail GROUP BY prd_no",
            catalog,
        )
        assert "NAME_FIELD_IN_GROUP_BY" not in result.codes


class TestPercentageMetric:
    """R-PCT-001 / C-07：百分比指标返回 0~1，禁止 * 100。"""

    def test_percentage_scaled_by_100_is_rejected(self, catalog: Catalog) -> None:
        result = _validate(
            "SELECT SUM(CASE WHEN fiv_lev_cla_flag IN ('30','40','50') "
            "THEN current_balnc_cny ELSE 0 END) / NULLIF(SUM(current_balnc_cny), 0) "
            "* 100 AS npl_rate FROM qa_loan_acc_detail",
            catalog,
            percentage_aliases={"npl_rate"},
        )
        assert not result.ok
        assert "PERCENTAGE_METRIC_SCALED" in result.codes

    def test_percentage_without_scaling_passes(self, catalog: Catalog) -> None:
        result = _validate(
            "SELECT SUM(CASE WHEN fiv_lev_cla_flag IN ('30','40','50') "
            "THEN current_balnc_cny ELSE 0 END) / NULLIF(SUM(current_balnc_cny), 0) "
            "AS npl_rate FROM qa_loan_acc_detail",
            catalog,
            percentage_aliases={"npl_rate"},
        )
        assert result.ok, result.feedback()

    def test_alias_from_intent_is_used_when_not_passed(self, catalog: Catalog) -> None:
        intent = Intent(metric="npl_rate", time_range=TimeRange(grain="month_end"))
        result = _validate(
            "SELECT SUM(current_balnc_cny) / NULLIF(SUM(ecl_final), 0) * 100 AS npl_rate "
            "FROM qa_loan_acc_detail",
            catalog,
            intent=intent,
        )
        assert "PERCENTAGE_METRIC_SCALED" in result.codes


class TestComparisonShape:
    """R-COMP-001 / C-05：多时点对比禁止用子查询查另一时点。"""

    def test_scalar_subquery_on_other_snapshot_is_rejected(self, catalog: Catalog) -> None:
        sql = """
            SELECT SUM(current_balnc_cny) AS cur_bal,
                   (SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail
                     WHERE etl_dt = DATE '2024-12-31') AS base_bal
            FROM qa_loan_acc_detail
            WHERE etl_dt = DATE '2025-12-31'
        """
        result = _validate(sql, catalog)
        assert not result.ok
        assert "COMPARISON_USES_SUBQUERY" in result.codes

    def test_in_plus_case_when_passes(self, catalog: Catalog) -> None:
        sql = """
            SELECT SUM(CASE WHEN etl_dt = DATE '2025-12-31'
                            THEN current_balnc_cny ELSE 0 END) AS cur_bal,
                   SUM(CASE WHEN etl_dt = DATE '2024-12-31'
                            THEN current_balnc_cny ELSE 0 END) AS base_bal
            FROM qa_loan_acc_detail
            WHERE etl_dt IN (DATE '2025-12-31', DATE '2024-12-31')
        """
        result = _validate(sql, catalog)
        assert "COMPARISON_USES_SUBQUERY" not in result.codes

    def test_single_point_with_subquery_is_not_flagged(self, catalog: Catalog) -> None:
        """只有一个时点时，子查询可能是"取最大值"之类，不该误报。"""
        sql = """
            SELECT SUM(current_balnc_cny) AS bal
            FROM qa_loan_acc_detail
            WHERE etl_dt = (SELECT MAX(etl_dt) FROM qa_loan_acc_detail)
        """
        result = _validate(sql, catalog)
        assert "COMPARISON_USES_SUBQUERY" not in result.codes


class TestMonthlyTopN:
    """R-TOPN-001 / C-12："每月 Top N" 必须用窗口函数。"""

    def test_limit_only_is_rejected(self, catalog: Catalog) -> None:
        intent = Intent(
            metric="loan_balance",
            time_range=TimeRange(grain="month"),
            top_n=3,
            top_n_grain="month",
        )
        sql = (
            "SELECT org_no, SUM(current_balnc_cny) AS bal FROM qa_loan_acc_detail "
            "GROUP BY org_no ORDER BY bal DESC LIMIT 3"
        )
        result = _validate(sql, catalog, intent=intent)
        assert not result.ok
        assert "TOPN_MISSING_WINDOW" in result.codes

    def test_window_function_passes(self, catalog: Catalog) -> None:
        intent = Intent(
            metric="loan_balance",
            time_range=TimeRange(grain="month"),
            top_n=3,
            top_n_grain="month",
        )
        sql = """
            WITH agg AS (
                SELECT org_no, etl_dt, SUM(current_balnc_cny) AS bal
                FROM qa_loan_acc_detail
                GROUP BY org_no, etl_dt
            ), ranked AS (
                SELECT org_no, etl_dt, bal,
                       ROW_NUMBER() OVER (PARTITION BY etl_dt ORDER BY bal DESC) AS rn
                FROM agg
            )
            SELECT org_no, etl_dt, bal FROM ranked WHERE rn <= 3
        """
        result = _validate(sql, catalog, intent=intent)
        assert "TOPN_MISSING_WINDOW" not in result.codes

    def test_no_intent_means_no_check(self, catalog: Catalog) -> None:
        """没有意图信息时不猜 —— 否则任何带 LIMIT 的查询都会被误报。"""
        sql = (
            "SELECT org_no, SUM(current_balnc_cny) AS bal FROM qa_loan_acc_detail "
            "GROUP BY org_no ORDER BY bal DESC LIMIT 3"
        )
        result = _validate(sql, catalog)
        assert "TOPN_MISSING_WINDOW" not in result.codes


class TestTableCount:
    """R-TBL-001 / C-04：明细分支最多 2 张表。"""

    def test_three_tables_rejected_in_plain_mode(self, catalog: Catalog) -> None:
        sql = """
            SELECT l.borrow_note_no, w.writeoff_principal, c.current_balance
            FROM qa_loan_acc_detail l
            JOIN qa_writeoff_detail w ON w.source_account_no = l.borrow_note_no
            JOIN qa_credit_card_detail c ON c.card_account_no = l.borrow_note_no
        """
        result = _validate(sql, catalog, intent=Intent(metric="x", source_mode="plain"))
        assert not result.ok
        assert "TOO_MANY_TABLES" in result.codes

    def test_two_tables_allowed(self, catalog: Catalog) -> None:
        sql = """
            SELECT l.borrow_note_no, w.writeoff_principal
            FROM qa_loan_acc_detail l
            JOIN qa_writeoff_detail w ON w.source_account_no = l.borrow_note_no
        """
        result = _validate(sql, catalog, intent=Intent(metric="x", source_mode="plain"))
        assert "TOO_MANY_TABLES" not in result.codes


class TestReportJoin:
    """R-JOIN-001 / C-06：报表分支禁止 JOIN，多报表走 UNION ALL。"""

    def test_join_in_report_mode_is_rejected(self, catalog: Catalog) -> None:
        sql = """
            SELECT a.prd_no, a.npl_rate, b.npl_rate
            FROM rpt_npl_by_product_202604 a
            JOIN rpt_npl_by_product_202605 b ON b.prd_no = a.prd_no
        """
        result = _validate(sql, catalog, intent=Intent(metric="npl_rate", source_mode="report"))
        assert not result.ok
        assert "JOIN_NOT_ALLOWED" in result.codes

    def test_union_all_in_report_mode_passes(self, catalog: Catalog) -> None:
        sql = """
            SELECT prd_no, npl_rate FROM rpt_npl_by_product_202604
            UNION ALL
            SELECT prd_no, npl_rate FROM rpt_npl_by_product_202605
        """
        result = _validate(sql, catalog, intent=Intent(metric="npl_rate", source_mode="report"))
        assert "JOIN_NOT_ALLOWED" not in result.codes


class TestPolicyIsConfigurable:
    def test_snapshot_column_can_be_renamed(self, catalog: Catalog) -> None:
        """换一家银行换成 ``snap_date``，校验器不用改。"""
        policy = ValidationPolicy(snapshot_column="snap_date")
        sql = """
            SELECT (SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail
                     WHERE snap_date = DATE '2024-12-31') AS base_bal
            FROM qa_loan_acc_detail
            WHERE snap_date = DATE '2025-12-31'
        """
        result = _validate(sql, catalog, policy=policy)
        assert "COMPARISON_USES_SUBQUERY" in result.codes

    def test_name_suffixes_can_be_configured(self, catalog: Catalog) -> None:
        policy = ValidationPolicy(name_field_suffixes=("_name",))
        result = _validate(
            "SELECT prd_nm, SUM(current_balnc_cny) FROM qa_loan_acc_detail GROUP BY prd_nm",
            catalog,
            policy=policy,
        )
        # 后缀改成 _name 后，prd_nm 不再是"名称字段"
        assert "NAME_FIELD_IN_GROUP_BY" not in result.codes


class TestProblemCap:
    def test_problems_are_capped_but_keep_fatal(self, catalog: Catalog) -> None:
        policy = ValidationPolicy(max_problems=2)
        # 4 个未知列 -> 4 条问题，被截断到 2 条 + 1 条截断提示
        sql = "SELECT bad1, bad2, bad3, bad4 FROM qa_loan_acc_detail"
        result = _validate(sql, catalog, policy=policy)
        assert len(result.problems) == 3
        assert "PROBLEMS_TRUNCATED" in result.codes
        assert result.codes.count("UNKNOWN_COLUMN") == 2

    def test_fatal_survives_the_cap(self, catalog: Catalog) -> None:
        """致命错误不能被一堆 warning 挤掉。"""
        policy = ValidationPolicy(max_problems=1)
        result = _validate("DELETE FROM qa_loan_acc_detail", catalog, policy=policy)
        assert not result.ok
        assert result.problems[0].severity == "fatal"


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail", True),
        ("SELECT prd_nm FROM qa_loan_acc_detail GROUP BY prd_nm", False),
    ],
)
def test_ok_flag_matches_expectation(catalog: Catalog, sql: str, expected: bool) -> None:
    assert _validate(sql, catalog).ok is expected
