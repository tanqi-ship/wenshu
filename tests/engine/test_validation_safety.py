"""只读与副作用校验。"""

from __future__ import annotations

import pytest

from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import ValidationPolicy
from app.engine.validation.safety import parse_single_statement
from tests.constants import DIALECT


def _codes(result) -> list[str]:
    return result.codes


class TestOnlyReadQueries:
    def test_select_is_accepted(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) AS bal FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert result.ok, result.feedback()

    def test_cte_select_is_accepted(self, catalog: Catalog) -> None:
        result = validate_sql(
            "WITH x AS (SELECT current_balnc_cny FROM qa_loan_acc_detail) "
            "SELECT SUM(current_balnc_cny) FROM x",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert result.ok, result.feedback()

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM qa_loan_acc_detail",
            "UPDATE qa_loan_acc_detail SET current_balnc_cny = 0",
            "DROP TABLE qa_loan_acc_detail",
            "TRUNCATE TABLE qa_loan_acc_detail",
        ],
    )
    def test_write_statements_are_rejected(self, catalog: Catalog, sql: str) -> None:
        result = validate_sql(sql, catalog=catalog, dialect=DIALECT)
        assert not result.ok
        assert "SQL_NOT_READ_QUERY" in _codes(result)

    def test_multiple_statements_are_rejected(self, catalog: Catalog) -> None:
        result = validate_sql("SELECT 1; DROP TABLE qa_loan_acc_detail", catalog=catalog, dialect=DIALECT)
        assert not result.ok
        assert "SQL_MULTI_STATEMENT" in _codes(result)

    def test_unparsable_sql_is_rejected(self, catalog: Catalog) -> None:
        result = validate_sql("SELECT FROM WHERE ((", catalog=catalog, dialect=DIALECT)
        assert not result.ok
        assert "SQL_PARSE_ERROR" in _codes(result)

    def test_empty_sql_is_rejected(self, catalog: Catalog) -> None:
        result = validate_sql("   ", catalog=catalog, dialect=DIALECT)
        assert not result.ok


class TestForbiddenFunctions:
    @pytest.mark.parametrize(
        "func",
        ["pg_sleep(1)", "pg_read_file('/etc/passwd')", "dblink('x','y')"],
    )
    def test_side_effecting_functions_are_rejected(self, catalog: Catalog, func: str) -> None:
        result = validate_sql(f"SELECT {func}", catalog=catalog, dialect=DIALECT)
        assert not result.ok
        assert "SQL_FORBIDDEN_FUNCTION" in _codes(result)

    def test_normal_aggregate_is_allowed(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert "SQL_FORBIDDEN_FUNCTION" not in _codes(result)

    def test_sequence_nextval_is_rejected(self, catalog: Catalog) -> None:
        result = validate_sql(
            "SELECT nextval('seq_x') AS v FROM qa_loan_acc_detail",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert not result.ok
        assert "SQL_FORBIDDEN_FUNCTION" in _codes(result)


class TestLockingRead:
    def test_for_update_is_not_a_plain_read(self, catalog: Catalog) -> None:
        """``SELECT ... FOR UPDATE`` 会加锁，不属于只读查询。

        这条是宽松断言：不同 sqlglot 版本对 Lock 的建模方式不同，
        但"能不能执行"这个结论必须是否。
        """
        result = validate_sql(
            "SELECT current_balnc_cny FROM qa_loan_acc_detail FOR UPDATE",
            catalog=catalog,
            dialect=DIALECT,
        )
        assert not result.ok


class TestParseSingleStatement:
    def test_returns_statement_for_valid_sql(self) -> None:
        statement, problems = parse_single_statement("SELECT 1", DIALECT)
        assert statement is not None
        assert problems == []

    def test_fatal_problems_block_further_checks(self, catalog: Catalog, policy: ValidationPolicy) -> None:
        """解析失败时不应继续跑业务规则 —— 否则会看到一堆莫名其妙的警告。"""
        result = validate_sql("NOT SQL AT ALL", catalog=catalog, dialect=DIALECT, policy=policy)
        assert result.codes == ["SQL_PARSE_ERROR"]
