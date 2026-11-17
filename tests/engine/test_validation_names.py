"""名字校验与检索范围校验。"""

from __future__ import annotations

from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import Scope
from tests.constants import DIALECT


class TestTableNames:
    def test_unknown_table_suggests_close_match(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detial",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert not result.ok
        problem = next(p for p in result.problems if p.code == "UNKNOWN_TABLE")
        # 提示里必须带上正确名字，否则模型只能瞎猜
        assert "qa_loan_acc_detail" in problem.message

    def test_existing_table_passes(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert "UNKNOWN_TABLE" not in result.codes

    def test_ambiguous_table_across_schemas(self) -> None:
        catalog = Catalog.from_mapping(
            {
                "etl": {"t_loan": ["amt"]},
                "rpt": {"t_loan": ["amt"]},
            }
        )
        result = validate_sql(
            "SELECT SUM(amt) FROM t_loan", catalog=catalog, dialect=DIALECT
        )
        assert not result.ok
        assert "AMBIGUOUS_TABLE" in result.codes

    def test_schema_qualified_ambiguous_table_passes(self) -> None:
        catalog = Catalog.from_mapping(
            {
                "etl": {"t_loan": ["amt"]},
                "rpt": {"t_loan": ["amt"]},
            }
        )
        result = validate_sql(
            "SELECT SUM(amt) FROM etl.t_loan", catalog=catalog, dialect=DIALECT
        )
        assert "AMBIGUOUS_TABLE" not in result.codes


class TestColumnNames:
    def test_unknown_column_suggests_close_match(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(current_balanc_cny) FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert not result.ok
        problem = next(p for p in result.problems if p.code == "UNKNOWN_COLUMN")
        assert "current_balnc_cny" in problem.message

    def test_star_is_not_flagged(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT COUNT(*) FROM qa_loan_acc_detail", catalog=catalog, dialect=DIALECT
        )
        assert "UNKNOWN_COLUMN" not in result.codes

    def test_cte_alias_is_not_treated_as_table(self, catalog: Catalog) -> None:
        result = validate_sql(
            "WITH x AS (SELECT current_balnc_cny FROM qa_loan_acc_detail) "
            "SELECT SUM(current_balnc_cny) FROM x",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert "UNKNOWN_TABLE" not in result.codes

    def test_output_alias_is_referenceable(self, catalog: Catalog) -> None:
        """外层引用内层定义的别名是合法的，不能误报。"""
        result = validate_sql(
            "SELECT bal FROM (SELECT SUM(current_balnc_cny) AS bal "
            "FROM qa_loan_acc_detail) t",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert "UNKNOWN_COLUMN" not in result.codes

    def test_qualified_column_checked_against_its_table(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(t.current_balance) FROM qa_loan_acc_detail t",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert not result.ok
        assert "UNKNOWN_COLUMN" in result.codes


class TestScope:
    def test_table_outside_scope_is_rejected(self, catalog: Catalog) -> None:
        """表是真的，但检索没给过它 —— 这和"拼错名字"是两种不同的错误。"""
        scope = Scope.of(["qa_loan_acc_detail"])
        result = validate_sql(
            "SELECT SUM(current_balance) FROM qa_credit_card_detail",
            catalog=catalog,
            dialect=DIALECT,
            scope=scope,
        )
        assert not result.ok
        assert "TABLE_NOT_IN_SCOPE" in result.codes
        assert "UNKNOWN_TABLE" not in result.codes

    def test_table_inside_scope_passes(self, catalog: Catalog) -> None:
        scope = Scope.of(["qa_loan_acc_detail"])
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
            scope=scope,
        )
        assert result.ok, result.feedback()

    def test_empty_scope_does_not_restrict(self, catalog: Catalog) -> None:
        """空 Scope 表示"未限定"，不是"什么都不许用"。

        否则忘记传 scope 会把所有查询都判死，调试时非常难查。
        """
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
            scope=Scope(),
        )
        assert result.ok, result.feedback()
