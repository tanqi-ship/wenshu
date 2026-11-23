"""独立手写 SQL 攻击矩阵，不用编译器充当规则的标准答案。"""

import ast
from pathlib import Path

import pytest
from sqlglot import parse_one

from app.engine.validation import ValidationPolicy, validate_sql
from app.engine.validation.catalog import Catalog
from app.engine.validation.org import org_match_not_exact

TABLE = "qa_loan_acc_detail"
CATALOG = Catalog.from_mapping(
    {
        "public": {
            TABLE: ["org_no", "org_nm", "accounting_org_no", "amount", "value", "flag", "etl_dt", "prd_no"],
            "dim_org": ["org_no", "org_nm", "parent_org_no"],
            "ordinary": ["org_no", "org_nm", "amount", "value", "flag"],
        }
    }
)


def problems(sql, *, catalog=CATALOG, policy=None, dialect=None):
    return org_match_not_exact(parse_one(sql, read=dialect), catalog, policy or ValidationPolicy())


def check(condition):
    return problems(f"SELECT t.amount FROM {TABLE} t WHERE {condition}")


@pytest.mark.parametrize(
    "condition",
    [
        "t.org_no = 'B001'",
        "'B001' = t.org_no",
        "(t.org_no) = ('B001')",
        "t.org_no IN ('B001','B002')",
        "t.org_nm = 'A%_机构'",
        "t.org_nm IN ('A%_机构', 'X')",
        "t.org_no = 'B001' OR t.org_no = 'B002'",
        "t.org_no = 'B001' AND t.amount > 0",
        "t.amount BETWEEN 1 AND 100",
        "t.prd_no LIKE 'LP%'",
        "t.amount IS NOT NULL",
        "1 = 0",
    ],
)
def test_direct_exact_and_unrelated_conditions_allowed(condition):
    assert not check(condition)


@pytest.mark.parametrize(
    "condition",
    [
        "t.org_no LIKE 'B001%'",
        "t.org_nm ILIKE '%渝北%'",
        "t.org_no NOT LIKE 'B%'",
        "t.org_no <> 'B001'",
        "t.org_no != 'B001'",
        "NOT t.org_no = 'B001'",
        "NOT (t.org_no = 'B001' OR t.amount = 0)",
        "t.org_no NOT IN ('B001')",
        "t.org_no BETWEEN 'B001' AND 'B999'",
        "t.org_no > 'B001'",
        "t.org_no IS NULL",
        "t.org_no IS NOT NULL",
        "LOWER(t.org_no) = 'b001'",
        "'b001' = LOWER(t.org_no)",
        "TRIM(t.org_no) = 'B001'",
        "CAST(t.org_no AS TEXT) = 'B001'",
        "SUBSTRING(t.org_no,1,4) = 'B001'",
        "REPLACE(t.org_no,'9','') = 'B001'",
        "CONCAT(t.org_no,'') = 'B001'",
        "CUSTOM_FN(t.org_no) = 'B001'",
        "INSTR(t.org_nm,'渝北') > 0",
        "STRPOS(t.org_nm,'渝北') > 0",
        "REGEXP_LIKE(t.org_nm,'渝北.*')",
        "STARTS_WITH(t.org_no,'B001')",
        "ENDS_WITH(t.org_nm,'支行')",
        "CONTAINS(t.org_nm,'渝北')",
        "t.org_no = 1",
        "t.org_no IN (1,2)",
        "t.org_no IN ('B001', NULL)",
        "t.org_no IN ('B' || '001')",
        "t.org_no IN (SELECT org_no FROM dim_org)",
        "t.org_no = (SELECT org_no FROM dim_org)",
        "t.org_no = UPPER('b001')",
        "CUSTOM_PREDICATE(t.org_no)",
        "CASE WHEN t.org_no = 'B001' THEN 'X' ELSE 'Y' END = 'X'",
    ],
)
def test_non_exact_or_transformed_conditions_rejected(condition):
    issues = check(condition)
    assert any(p.code == "ORG_MATCH_NOT_EXACT" and p.rule == "R-ORG-001" for p in issues), issues


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT CASE WHEN org_nm LIKE '%支行%' THEN amount ELSE 0 END FROM {TABLE}",
        f"SELECT SUM(CASE WHEN org_nm LIKE '%支行%' THEN amount ELSE 0 END) FROM {TABLE}",
        f"SELECT COUNT(*) FROM {TABLE} HAVING MAX(org_nm) LIKE '%支行%'",
        f"SELECT t.amount FROM {TABLE} t JOIN dim_org d ON t.org_no LIKE d.org_no",
        f"SELECT SUM(amount) FILTER (WHERE org_nm LIKE '%支行%') FROM {TABLE}",
        f"SELECT org_nm LIKE '%支行%' AS flag FROM {TABLE}",
    ],
)
def test_conditions_in_all_positions_checked(sql):
    assert any(p.code == "ORG_MATCH_NOT_EXACT" for p in problems(sql))


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT t.org_no, MAX(t.org_nm), SUM(t.amount) FROM {TABLE} t GROUP BY t.org_no",
        f"SELECT t.amount FROM {TABLE} t JOIN dim_org d ON t.org_no = d.org_no WHERE d.parent_org_no = 'H000'",
        f"SELECT t.amount FROM {TABLE} t JOIN ordinary d ON t.org_no = d.org_no",
        f"SELECT SUM(CASE WHEN org_no = 'B001' THEN amount ELSE 0 END) FROM {TABLE}",
        f"SELECT COUNT(org_no) AS n FROM {TABLE} HAVING COUNT(org_no) > 0",
        f"SELECT q.n FROM (SELECT COUNT(org_no) AS n FROM {TABLE}) q WHERE q.n > 0",
        f"SELECT q.n FROM (SELECT AVG(amount) AS n FROM {TABLE}) q WHERE q.n > 0",
        f"SELECT q.rn FROM (SELECT ROW_NUMBER() OVER (PARTITION BY org_no ORDER BY org_nm) AS rn FROM {TABLE}) q WHERE q.rn <= 2",
        f"SELECT q.n FROM (SELECT SUM(amount) OVER (PARTITION BY org_no ORDER BY org_nm) AS n FROM {TABLE}) q WHERE q.n > 0",
        f"SELECT MAX(LOWER(org_nm)) FROM {TABLE}",
        f"SELECT q.code FROM (SELECT org_no AS code FROM {TABLE}) q WHERE q.code = 'B001'",
        f"WITH x(code) AS (SELECT org_no FROM {TABLE}) SELECT code FROM x WHERE code = 'B001'",
        f"SELECT q.code FROM (SELECT org_no FROM {TABLE}) q(code) WHERE q.code = 'B001'",
        f"SELECT q.org_no FROM (SELECT * FROM {TABLE}) q WHERE q.org_no IN ('B001')",
        f"SELECT q.org_no FROM (SELECT org_no FROM {TABLE} UNION ALL SELECT org_no FROM dim_org) q WHERE q.org_no = 'B001'",
        "SELECT org_no FROM ordinary WHERE LOWER(org_no) LIKE 'b%'",
    ],
)
def test_projection_join_aggregation_and_exact_derived_sources_allowed(sql):
    assert not problems(sql)


@pytest.mark.parametrize(
    "inner,outer",
    [
        (f"SELECT org_no AS code FROM {TABLE}", "q.code LIKE 'B%'"),
        (f"SELECT LOWER(org_no) AS code FROM {TABLE}", "q.code = 'b001'"),
        (f"SELECT CUSTOM_FN(org_no) AS code FROM {TABLE}", "q.code = 'B001'"),
        (f"SELECT MAX(org_nm) AS code FROM {TABLE}", "q.code = '渝北支行'"),
        (f"SELECT org_no AS code FROM {TABLE} UNION ALL SELECT 'B001' AS code", "q.code = 'B001'"),
        (f"SELECT 'B001' AS code UNION ALL SELECT org_no AS code FROM {TABLE}", "q.code = 'B001'"),
        (
            f"SELECT org_no AS code FROM {TABLE} UNION ALL SELECT LOWER(org_no) FROM dim_org",
            "q.code = 'B001'",
        ),
        (f"SELECT * FROM {TABLE}", "q.org_nm LIKE '%支行%'"),
        (f"SELECT t.org_no AS code FROM {TABLE} t", "q.code IN ('B' || '001')"),
    ],
)
def test_protection_survives_rename_transform_star_and_union(inner, outer):
    assert any(
        p.code in {"ORG_MATCH_NOT_EXACT", "ORG_MATCH_SOURCE_UNRESOLVED"}
        for p in problems(f"SELECT * FROM ({inner}) q WHERE {outer}")
    )


@pytest.mark.parametrize(
    "sql",
    [
        f"WITH x(code) AS (SELECT org_no FROM {TABLE}) SELECT code FROM x WHERE code LIKE 'B%'",
        f"WITH x AS (SELECT LOWER(org_no) code FROM {TABLE}), y AS (SELECT code AS hidden FROM x) SELECT hidden FROM y WHERE hidden = 'B001'",
        f"SELECT q.code FROM (SELECT org_no FROM {TABLE}) q(code) WHERE q.code LIKE 'B%'",
        f"SELECT MAX(org_nm) AS hidden FROM {TABLE} HAVING hidden = '渝北支行'",
    ],
)
def test_cte_and_output_aliases_do_not_hide_transformation(sql):
    assert any(p.code == "ORG_MATCH_NOT_EXACT" for p in problems(sql))


def test_same_alias_in_nested_scopes_cannot_hide_protected_outer_column():
    sql = f"SELECT q.org_no FROM {TABLE} q WHERE q.org_no LIKE 'B%' AND EXISTS (SELECT 1 FROM ordinary q WHERE q.org_no = 'x')"
    assert any(p.code == "ORG_MATCH_NOT_EXACT" for p in problems(sql))
    clean = f"SELECT q.org_no FROM ordinary q WHERE q.org_no LIKE 'B%' AND EXISTS (SELECT 1 FROM {TABLE} q WHERE q.org_no = 'B001')"
    assert not problems(clean)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT t.amount FROM {TABLE} t JOIN dim_org d ON t.org_no=d.org_no WHERE org_no='B001'",
        f"SELECT q.code FROM (SELECT t.org_no AS code, d.org_no AS code FROM {TABLE} t JOIN dim_org d ON t.org_no=d.org_no) q WHERE q.code='B001'",
        f"WITH RECURSIVE x(code) AS (SELECT org_no FROM {TABLE} UNION ALL SELECT code FROM x) SELECT code FROM x WHERE code='B001'",
        f"SELECT t.amount FROM {TABLE} t WHERE EXISTS (SELECT 1 FROM ordinary o WHERE o.org_no=t.org_no)",
    ],
)
def test_uncertain_org_sources_fail_closed(sql):
    assert any(p.code == "ORG_MATCH_SOURCE_UNRESOLVED" for p in problems(sql))


def test_schema_ambiguity_cannot_disable_protection():
    catalog = Catalog.from_mapping({"public": {TABLE: ["org_no"]}, "other": {TABLE: ["org_no"]}})
    assert any(
        p.code == "ORG_MATCH_SOURCE_UNRESOLVED"
        for p in problems(f"SELECT org_no FROM {TABLE} WHERE org_no='B001'", catalog=catalog)
    )
    assert not problems(f"SELECT org_no FROM other.{TABLE} WHERE org_no LIKE 'B%'", catalog=catalog)


def test_explicit_custom_schema_column_protection():
    catalog = Catalog.from_mapping({"main": {"report": ["branch_code", "org_no"]}})
    policy = ValidationPolicy(org_columns={"main.report": frozenset({"branch_code"})})
    assert problems(
        "SELECT branch_code FROM report WHERE branch_code LIKE 'B%'", catalog=catalog, policy=policy
    )
    assert not problems("SELECT org_no FROM report WHERE org_no LIKE 'B%'", catalog=catalog, policy=policy)


def test_empty_trusted_policy_is_explicit_opt_out():
    assert not problems(
        f"SELECT org_no FROM {TABLE} WHERE org_no LIKE 'B%'", policy=ValidationPolicy(org_columns={})
    )


def test_policy_copies_and_freezes_configuration():
    values = {"org_no"}
    mapping = {"PUBLIC.QA_LOAN_ACC_DETAIL": values}
    policy = ValidationPolicy(org_columns=mapping)
    values.add("amount")
    mapping.clear()
    assert policy.org_columns == {"public.qa_loan_acc_detail": frozenset({"org_no"})}
    with pytest.raises(TypeError):
        policy.org_columns["other.table"] = frozenset()


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        [],
        {"t": {"org_no"}},
        {"public.t;drop": {"org_no"}},
        {"public.t": "org_no"},
        {"public.t": {"x)"}},
        {"public.t": {1}},
        {1: {"org_no"}},
    ],
)
def test_bad_policy_rejected(mapping):
    with pytest.raises(ValueError):
        ValidationPolicy(org_columns=mapping)


def test_pipeline_runs_rule_without_intent():
    result = validate_sql(f"SELECT org_no FROM {TABLE} WHERE org_no LIKE 'B%'", catalog=CATALOG)
    assert "ORG_MATCH_NOT_EXACT" in result.codes
    assert not result.ok


def test_rule_has_no_compiler_or_semantic_dependency():
    root = Path(__file__).resolve().parents[2] / "app/engine/validation"
    for file in [root / "org.py", root / "org_sources.py"]:
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("app.engine.compiler", "app.engine.semantic"))


@pytest.mark.parametrize(
    "dialect,condition",
    [
        ("postgres", "org_nm ~ '渝北.*'"),
        ("postgres", "org_no SIMILAR TO 'B001%'"),
        ("postgres", "org_no IS NOT DISTINCT FROM 'B001'"),
        ("oracle", "REGEXP_LIKE(org_nm, '渝北.*')"),
        ("sqlite", "org_nm GLOB '渝北*'"),
        ("sqlite", "org_no COLLATE NOCASE = 'b001'"),
    ],
)
def test_dialect_matching_variants_are_not_a_bypass(dialect, condition):
    assert problems(f"SELECT org_no FROM {TABLE} WHERE {condition}", dialect=dialect)


def test_table_alias_case_collision_is_not_last_write_wins():
    sql = f"SELECT x.org_no FROM {TABLE} X JOIN ordinary x ON 1=1 WHERE x.org_no LIKE 'B%'"
    assert any(p.code == "ORG_MATCH_SOURCE_UNRESOLVED" for p in problems(sql))


def test_cte_reference_column_aliases_keep_exact_and_transformed_states():
    assert not problems(
        f"WITH x AS (SELECT org_no FROM {TABLE}) SELECT q.code FROM x AS q(code) WHERE q.code='B001'"
    )
    assert problems(
        f"WITH x AS (SELECT LOWER(org_no) FROM {TABLE}) SELECT q.code FROM x AS q(code) WHERE q.code='B001'"
    )


def test_simple_case_implicit_matching_is_also_checked():
    assert not problems(f"SELECT CASE org_no WHEN 'B001' THEN 1 ELSE 0 END FROM {TABLE}")
    assert problems(f"SELECT CASE LOWER(org_no) WHEN 'b001' THEN 1 ELSE 0 END FROM {TABLE}")


def test_rule_does_not_mutate_ast():
    statement = parse_one(
        f"WITH x AS (SELECT org_no FROM {TABLE}) SELECT CASE code WHEN 'B001' THEN 1 ELSE 0 END FROM x"
    )
    original = statement.copy()
    org_match_not_exact(statement, CATALOG, ValidationPolicy())
    assert statement == original


@pytest.mark.parametrize(
    "source",
    [
        "UNNEST(ARRAY[t.org_no]) AS q(code)",
        "(SELECT t.org_no AS code) q",
        "(VALUES (t.org_no)) q(code)",
    ],
)
@pytest.mark.parametrize("predicate", ["q.code LIKE 'B%'", "q.code='B001'"])
def test_lateral_and_correlated_table_functions_fail_closed(source, predicate):
    sql = f"SELECT q.code FROM {TABLE} t CROSS JOIN LATERAL {source} WHERE {predicate}"
    assert any(p.code == "ORG_MATCH_SOURCE_UNRESOLVED" for p in problems(sql, dialect="postgres"))


def test_unrelated_lateral_literal_does_not_taint_all_complex_queries():
    sql = f"SELECT q.code FROM {TABLE} t CROSS JOIN LATERAL UNNEST(ARRAY['x']) AS q(code) WHERE q.code LIKE 'x%'"
    assert not problems(sql, dialect="postgres")


def test_output_alias_resolution_never_leaks_across_query_or_filter_boundaries():
    attack = f"SELECT COUNT(*) FROM ordinary HAVING (SELECT SUM(CASE WHEN org_no LIKE 'B%' THEN amount ELSE 0 END) AS org_no FROM {TABLE}) > 0"
    assert problems(attack)
    clean = f"SELECT MAX(org_no) AS org_no FROM {TABLE} HAVING SUM(amount) FILTER (WHERE org_no='B001') > 0"
    assert not problems(clean)


@pytest.mark.parametrize(
    "sql",
    [
        f"WITH x(code) AS (SELECT org_no, amount FROM {TABLE}) SELECT code, amount FROM x WHERE code='B001' AND amount>0",
        f"SELECT q.code FROM (SELECT org_no, amount FROM {TABLE}) q(code) WHERE q.code='B001' AND q.amount>0",
    ],
)
def test_partial_column_alias_list_preserves_remaining_column_names(sql):
    assert not problems(sql)


def test_unrelated_recursive_numbers_are_not_org_values():
    sql = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<10) SELECT n FROM x WHERE n>2"
    assert not problems(sql)


def test_recursive_org_predicate_inside_step_also_has_uncertain_source():
    sql = f"WITH RECURSIVE x(code) AS (SELECT org_no FROM {TABLE} UNION ALL SELECT code FROM x WHERE code='B001') SELECT code FROM x"
    assert any(p.code == "ORG_MATCH_SOURCE_UNRESOLVED" for p in problems(sql))
