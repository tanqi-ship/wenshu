"""M2.5：整体分母、展示过滤、整数除法和行权限的独立结果验收。"""

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import (
    ORG_TABLE,
    TABLE,
    TABLE_COLUMNS,
    semantic_catalog,
    validation_catalog,
)
from app.engine.semantic.dimension import LevelDef
from app.engine.semantic.models import Filter, Intent, ShareSpec, TimeRange
from app.engine.validation import ValidationPolicy, validate_sql
from app.engine.validation.catalog import Catalog


def share_intent(**changes):
    return replace(
        Intent(
            metric="贷款余额",
            time_range=month_end_range(2026, 4),
            dimensions=("产品",),
            share=ShareSpec("产品"),
        ),
        **changes,
    )


def catalog_with_metric(**changes):
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], **changes)
    return replace(base, metrics={**base.metrics, "loan_balance": metric})


def assert_error(code, *, intent=None, catalog=None, stage="intent", **kwargs):
    with pytest.raises(CompileError) as error:
        compile_intent(
            intent if intent is not None else share_intent(),
            catalog if catalog is not None else semantic_catalog(),
            **kwargs,
        )
    assert (error.value.stage, error.value.code) == (stage, code)


@pytest.fixture
def balances():
    db = sqlite3.connect(":memory:")
    db.execute(
        f"CREATE TABLE {TABLE} (etl_dt TEXT, org_no TEXT, org_nm TEXT, accounting_org_no TEXT, prd_no TEXT, prd_nm TEXT, fiv_lev_cla_flag TEXT, current_balnc_cny INTEGER, ecl_final INTEGER)"
    )
    db.executemany(
        f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        [
            ("2026-04-30", "B001", "渝北支行", "B001", "LP01", "个人住房贷款", "10", 100),
            ("2026-04-30", "B001", "渝北支行", "B001", "LP03", "小微企业贷款", "30", 300),
            ("2026-04-30", "B001", "渝北支行", "B001", "LP07", "消费贷款", "40", 600),
            ("2026-04-30", "B002", "另一支行", "B002", "LP03", "小微企业贷款", "10", 900),
            ("2026-03-31", "B001", "渝北支行", "B001", "LP03", "小微企业贷款", "30", 2000),
        ],
    )
    try:
        yield db
    finally:
        db.close()


def rows(db, *, intent=None, catalog=None, **kwargs):
    return db.execute(
        compile_sql(
            intent if intent is not None else share_intent(),
            catalog if catalog is not None else semantic_catalog(),
            **kwargs,
        )
    ).fetchall()


def branch_context(*extra):
    return share_intent(filters=(Filter("机构", "渝北支行"), *extra))


def test_structure_keeps_physical_context_inside_and_display_outside():
    intent = branch_context(Filter("产品", "小微企业贷款"))
    statement = compile_intent(intent, semantic_catalog())
    selects = list(statement.find_all(exp.Select))
    assert len(selects) == 3
    assert len(list(statement.find_all(exp.Table))) == 1
    assert selects[0].args["where"].sql() == "WHERE share_totals.prd_no = 'LP03'"
    assert "org_no = 'B001'" in selects[-1].args["where"].sql()
    assert "prd_no" not in selects[-1].args["where"].sql()
    assert selects[1].args.get("where") is None
    window = next(statement.find_all(exp.Window))
    assert not any(window.args.get(k) for k in ("partition_by", "order", "spec"))
    assert [p.alias_or_name for p in statement.expressions] == [
        "prd_no",
        "prd_nm",
        "loan_balance",
        "total_value",
        "share",
    ]
    check = validate_sql(statement.sql(dialect="sqlite"), catalog=validation_catalog(), intent=intent)
    assert check.ok, check.feedback()


def test_integer_amounts_and_single_product_display_do_not_rebase_total(balances):
    all_rows = rows(balances, intent=branch_context())
    assert [r[2:] for r in all_rows] == [(100, 1000, 0.1), (300, 1000, 0.3), (600, 1000, 0.6)]
    selected = rows(balances, intent=branch_context(Filter("产品", "LP03")))
    assert selected == [all_rows[1]]
    assert sum(r[-1] for r in all_rows) == pytest.approx(1)
    assert selected[0][-1] != 1


def test_row_policy_on_same_product_column_changes_total_not_display_semantics(balances):
    scope = Scope.of(
        [TABLE], row_policies=(RowPolicy(TABLE, "prd_no", values=("LP01", "LP03"), operator="in"),)
    )
    intent = branch_context(Filter("产品", "小微企业贷款"))
    result = rows(balances, intent=intent, scope=scope)
    assert result == [("LP03", "小微企业贷款", 300, 400, 0.75)]
    sql = compile_sql(intent, semantic_catalog(), scope=scope)
    check = validate_sql(sql, catalog=validation_catalog(), intent=intent, scope=scope)
    assert check.ok, check.feedback()
    assert "t1.prd_no IN ('LP01', 'LP03')" in sql
    assert "share_totals.prd_no = 'LP03'" in sql


def test_conditional_sum_denominator_is_same_measure_not_loan_balance(balances):
    intent = replace(branch_context(), metric="不良贷款余额")
    result = rows(balances, intent=intent)
    assert [r[2] for r in result] == [0, 300, 600]
    assert {r[3] for r in result} == {900}
    assert result[1][-1] == pytest.approx(1 / 3)


@pytest.mark.parametrize("policy", [RowPolicy(TABLE, "org_no"), RowPolicy(TABLE, "org_no", values=("B002",))])
def test_empty_or_disjoint_policy_returns_no_fabricated_rows(balances, policy):
    scope = Scope.of([TABLE], row_policies=(policy,))
    intent = branch_context()
    assert rows(balances, intent=intent, scope=scope) == []
    check = validate_sql(
        compile_sql(intent, semantic_catalog(), scope=scope),
        catalog=validation_catalog(),
        intent=intent,
        scope=scope,
    )
    assert check.ok, check.feedback()


def test_unused_table_policy_does_not_add_source_or_deny_current_rows(balances):
    scope = Scope.of([TABLE], row_policies=(RowPolicy(ORG_TABLE, "org_no"),))
    assert len(rows(balances, intent=branch_context(), scope=scope)) == 3


@pytest.mark.parametrize("values", [(0, 0, 0), (100, -100, 0), (None, None, None)])
def test_zero_or_null_total_returns_null_share_not_zero(balances, values):
    for code, value in zip(("LP01", "LP03", "LP07"), values, strict=True):
        balances.execute(
            f"UPDATE {TABLE} SET current_balnc_cny = ? WHERE org_no = 'B001' AND etl_dt = '2026-04-30' AND prd_no = ?",
            (value, code),
        )
    result = rows(balances, intent=branch_context())
    assert len(result) == 3
    assert all(row[-1] is None for row in result)


def test_null_group_and_negative_values_are_not_hidden_or_clamped(balances):
    balances.execute(f"UPDATE {TABLE} SET current_balnc_cny = NULL WHERE prd_no = 'LP01'")
    balances.execute(f"UPDATE {TABLE} SET current_balnc_cny = -600 WHERE prd_no = 'LP07'")
    result = rows(balances, intent=branch_context())
    assert result[0][2:] == (None, -300, None)
    assert result[1][-1] == -1
    assert result[2][-1] == 2


def test_empty_source_or_missing_snapshot_does_not_backfill(balances):
    assert rows(balances, intent=share_intent(time_range=month_end_range(2026, 2))) == []
    balances.execute(f"DELETE FROM {TABLE}")
    assert rows(balances) == []


@pytest.mark.parametrize(
    "filters",
    [
        (Filter("产品", [], "in"),),
        (Filter("机构", [], "in"),),
        (Filter("产品", "LP05"),),
        (Filter("产品", "LP03"), Filter("产品", "LP01")),
    ],
)
def test_empty_in_absent_group_or_conflicting_display_filters_return_no_rows(balances, filters):
    assert rows(balances, intent=share_intent(filters=filters)) == []


def test_none_filter_and_synonym_dimension_deduplication(balances):
    intent = share_intent(
        dimensions=("产品", "product", "贷款产品"),
        share=ShareSpec("产品类型"),
        filters=(Filter("unknown", None, "gte"),),
    )
    assert len(rows(balances, intent=intent)) == 3


@pytest.mark.parametrize("value", [["LP01", "LP03"], "LP03", ["LP03"]])
def test_in_filters_use_outer_number_column_and_keep_total(balances, value):
    result = rows(balances, intent=branch_context(Filter("产品", value, "in")))
    assert {r[3] for r in result} == {1000}


def test_names_group_by_ids_and_optional_display(balances):
    balances.execute(f"UPDATE {TABLE} SET prd_nm = '同名'")
    assert len(rows(balances, intent=branch_context())) == 3
    base = semantic_catalog()
    product = replace(base.dimensions["product"], levels={"default": LevelDef("prd_no")})
    catalog = replace(base, dimensions={**base.dimensions, "product": product})
    assert rows(balances, intent=branch_context(), catalog=catalog)[1] == ("LP03", 300, 1000, 0.3)


@pytest.mark.parametrize(
    "alias", ["custom", "share_groups", "share_totals", "SHARE_GROUPS", "qa_loan_acc_detail"]
)
def test_custom_aliases_do_not_hide_physical_names_from_l4(alias):
    intent = share_intent()
    statement = compile_intent(intent, semantic_catalog(), alias=alias)
    aliases = [
        statement.args["from_"].this.alias,
        statement.args["from_"].this.this.args["from_"].this.alias,
        next(statement.find_all(exp.Table)).alias,
    ]
    assert len({a.casefold() for a in aliases}) == 3
    check = validate_sql(statement.sql(dialect="sqlite"), catalog=validation_catalog(), intent=intent)
    assert check.ok, check.feedback()


def test_custom_snapshot_and_explicit_primary_binding(balances):
    balances.execute(f"ALTER TABLE {TABLE} RENAME COLUMN etl_dt TO snapshot_day")
    catalog = catalog_with_metric(formula=f"SUM({TABLE}.current_balnc_cny)")
    policy = ValidationPolicy(snapshot_column="snapshot_day")
    intent = branch_context(Filter("产品", "LP03"))
    result = rows(balances, intent=intent, catalog=catalog, policy=policy)
    assert result[0][-1] == 0.3
    names = Catalog.from_mapping(
        {"public": {TABLE: ["snapshot_day" if c == "etl_dt" else c for c in TABLE_COLUMNS]}}
    )
    check = validate_sql(
        compile_sql(intent, catalog, policy=policy), catalog=names, intent=intent, policy=policy
    )
    assert check.ok, check.feedback()


def test_duplicate_identical_month_end_is_normalized_without_mutating_intent():
    time = TimeRange("month_end", points=(date(2026, 4, 30), date(2026, 4, 30)))
    intent = share_intent(time_range=time)
    sql = compile_sql(intent, semantic_catalog())
    assert "t1.etl_dt = '2026-04-30'" in sql
    assert len(intent.time_range.points) == 2


@pytest.mark.parametrize(
    ("changes", "code", "stage"),
    [
        ({"share": True}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"share": ShareSpec("")}, "SHARE_DIMENSION_REQUIRED", "intent"),
        ({"dimensions": ()}, "SHARE_DIMENSION_MISMATCH", "intent"),
        ({"dimensions": ("产品", "机构")}, "SHARE_DIMENSION_MISMATCH", "intent"),
        ({"share": ShareSpec("机构")}, "SHARE_DIMENSION_MISMATCH", "intent"),
        ({"dimensions": ("unknown",)}, "UNKNOWN_DIMENSION", "symbol_table"),
        ({"share": ShareSpec("unknown")}, "UNKNOWN_DIMENSION", "symbol_table"),
        ({"metric": "unknown"}, "UNKNOWN_METRIC", "symbol_table"),
        ({"metric": "不良贷款率"}, "SHARE_METRIC_NOT_ADDITIVE", "type_check"),
        ({"source_mode": "report"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"source_mode": "union"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "delta"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "yoy"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "mom"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "rate"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"top_n": 3}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"top_n_grain": "month"}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"org": "渝北支行"}, "SHARE_CONTEXT_UNRESOLVED", "intent"),
        ({"filters": (Filter("产品", "不存在"),)}, "UNKNOWN_CODE", "symbol_table"),
        ({"filters": (Filter("unknown", "x"),)}, "UNKNOWN_DIMENSION", "symbol_table"),
        ({"filters": (Filter("上级机构", "F001"),)}, "SHARE_SHAPE_UNSUPPORTED", "intent"),
        ({"time_range": None}, "TIME_UNRESOLVED", "intent"),
        ({"time_range": TimeRange("month_end")}, "TIME_UNRESOLVED", "intent"),
        (
            {"time_range": TimeRange("month_end", points=(date(2026, 4, 29),))},
            "SHARE_TIME_UNSUPPORTED",
            "intent",
        ),
        (
            {"time_range": TimeRange("month_end", points=(date(2026, 4, 30), date(2026, 3, 31)))},
            "SHARE_TIME_UNSUPPORTED",
            "intent",
        ),
        (
            {"time_range": TimeRange("month_end", points=(date(2026, 4, 30),), start=date(2026, 4, 1))},
            "SHARE_TIME_UNSUPPORTED",
            "intent",
        ),
        ({"time_range": TimeRange("month_end", start=date(2026, 4, 30))}, "SHARE_TIME_UNSUPPORTED", "intent"),
        (
            {"time_range": TimeRange("range", start=date(2026, 4, 1), end=date(2026, 4, 30))},
            "SHARE_TIME_UNSUPPORTED",
            "intent",
        ),
        ({"time_range": TimeRange("day", points=(date(2026, 4, 30),))}, "SHARE_TIME_UNSUPPORTED", "intent"),
    ],
)
def test_share_errors_are_explicit(changes, code, stage):
    assert_error(code, stage=stage, intent=share_intent(**changes))


@pytest.mark.parametrize("dimension", ["产品", "机构"])
@pytest.mark.parametrize("operator", ["gte", "lte", "between"])
def test_unsupported_filter_does_not_degrade_to_eq(dimension, operator):
    assert_error(
        "SHARE_FILTER_UNSUPPORTED", intent=share_intent(filters=(Filter(dimension, "LP03", operator),))
    )


@pytest.mark.parametrize(
    "formula",
    [
        "AVG(current_balnc_cny)",
        "COUNT(*)",
        "SUM(DISTINCT current_balnc_cny)",
        "SUM(SUM(current_balnc_cny))",
        "SUM(current_balnc_cny) OVER ()",
        "SUM((SELECT 1))",
        "SUM(*)",
        "SUM(current_balnc_cny) / 100",
    ],
)
def test_non_additive_formula_rejected(formula):
    assert_error(
        "SHARE_METRIC_NOT_ADDITIVE", stage="type_check", catalog=catalog_with_metric(formula=formula)
    )


def test_percentage_unit_is_not_proof_of_additive_value():
    assert_error("SHARE_METRIC_NOT_ADDITIVE", stage="type_check", catalog=catalog_with_metric(unit="%"))


@pytest.mark.parametrize("source_tables", [(), (TABLE, ORG_TABLE), (TABLE, ORG_TABLE, "third")])
def test_unique_source_required(source_tables):
    assert_error(
        "UNKNOWN_SOURCE" if not source_tables else "SHARE_SHAPE_UNSUPPORTED",
        stage="symbol_table" if not source_tables else "intent",
        catalog=catalog_with_metric(source_tables=source_tables),
    )


def test_unknown_formula_qualifier_rejected_even_without_column_scope():
    assert_error(
        "SHARE_SHAPE_UNSUPPORTED", catalog=catalog_with_metric(formula="SUM(other.current_balnc_cny)")
    )


@pytest.mark.parametrize("code", ["total_value", "share", "prd_no", "prd_nm"])
def test_output_collisions_rejected(code):
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], code=code)
    catalog = replace(base, metrics={code: metric})
    assert_error("SHARE_OUTPUT_COLLISION", stage="type_check", catalog=catalog)


def test_ambiguous_context_binding_cannot_smuggle_early_product_filter():
    base = semantic_catalog()
    org = replace(base.dimensions["org"], levels={"financial": LevelDef("prd_no")})
    catalog = replace(base, dimensions={**base.dimensions, "org": org})
    assert_error(
        "SHARE_FILTER_UNSUPPORTED", catalog=catalog, intent=share_intent(filters=(Filter("机构", "B001"),))
    )


@pytest.mark.parametrize("scope", [Scope(), Scope.of([ORG_TABLE])])
def test_explicit_empty_or_foreign_table_scope_denies(scope):
    assert_error("METRIC_OUT_OF_SCOPE", stage="scope", scope=scope)


@pytest.mark.parametrize(
    "column", ["etl_dt", "prd_no", "prd_nm", "current_balnc_cny", "org_no", "fiv_lev_cla_flag"]
)
def test_all_physical_columns_checked_before_wrapping(column):
    scope = Scope.of(
        [TABLE],
        columns={TABLE: frozenset(TABLE_COLUMNS) - {column}},
        row_policies=(RowPolicy(TABLE, "fiv_lev_cla_flag", values=("30",)),),
    )
    assert_error(
        "COLUMN_NOT_IN_SCOPE", stage="scope", intent=branch_context(Filter("产品", "LP03")), scope=scope
    )


def test_explicit_empty_columns_deny():
    assert_error("COLUMN_NOT_IN_SCOPE", stage="scope", scope=Scope.of([TABLE], columns={TABLE: frozenset()}))


def test_policy_input_order_and_context_intersection_are_preserved():
    scope = Scope.of(
        [TABLE],
        row_policies=(
            RowPolicy(TABLE, "prd_no", values=("LP01", "LP03")),
            RowPolicy(TABLE, "org_no", values=("B001",)),
        ),
    )
    sql = compile_sql(branch_context(Filter("产品", "LP03")), semantic_catalog(), scope=scope)
    assert sql.index("t1.org_no = 'B001'") < sql.index("t1.prd_no IN") < sql.rindex("t1.org_no = 'B001'")


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "oracle"])
def test_dialect_render_roundtrip_is_not_external_db_acceptance(dialect):
    import sqlglot

    intent = branch_context(Filter("产品", "LP03"))
    sql = compile_sql(intent, semantic_catalog(), dialect=dialect)
    assert len(list(sqlglot.parse_one(sql, read=dialect).find_all(exp.Select))) == 3
    check = validate_sql(sql, catalog=validation_catalog(), intent=intent, dialect=dialect)
    assert check.ok, check.feedback()


@pytest.mark.parametrize("case_id", ["G-019", "G-020", "G-021", "G-022"])
def test_handwritten_goldens_execute_on_independent_integer_data(balances, case_id):
    from scripts.golden import load_cases

    directory = Path(__file__).resolve().parents[2] / "eval" / "golden"
    case = next(c for c in load_cases(directory) if c.id == case_id)
    expected = balances.execute(case.golden_sql).fetchall()
    actual = rows(balances, intent=case.intent, scope=case.scope)
    assert actual == expected
    if case_id == "G-020":
        assert actual == [("LP03", "小微企业贷款", 300, 1000, 0.3)]
    elif case_id == "G-021":
        assert actual == [("LP03", "小微企业贷款", 300, 400, 0.75)]
    elif case_id == "G-022":
        assert actual == []
    else:
        assert len(actual) == 3 and all(r[3] == 1900 for r in actual)


def test_generic_org_share_keeps_product_context_but_not_display_org_filter(balances):
    intent = share_intent(
        dimensions=("机构",),
        share=ShareSpec("org"),
        filters=(Filter("产品", "LP03"), Filter("机构", "渝北支行")),
    )
    assert rows(balances, intent=intent) == [("B001", "渝北支行", 300, 1200, 0.25)]
    check = validate_sql(compile_sql(intent, semantic_catalog()), catalog=validation_catalog(), intent=intent)
    assert check.ok, check.feedback()


def test_schema_qualified_table_case_insensitive_policies_and_scope(balances):
    table = f"main.{TABLE}"
    intent = branch_context(Filter("产品", "LP03"))
    catalog = catalog_with_metric(source_tables=(table,), formula=f"SUM({table}.current_balnc_cny)")
    scope = Scope.of(
        [table.upper()],
        columns={table.upper(): frozenset(TABLE_COLUMNS)},
        row_policies=(RowPolicy(table.upper(), "prd_no", values=("LP01", "LP03")),),
    )
    assert rows(balances, intent=intent, catalog=catalog, scope=scope)[0][-1] == 0.75
    names = Catalog.from_mapping({"main": {TABLE: list(TABLE_COLUMNS)}})
    check = validate_sql(compile_sql(intent, catalog, scope=scope), catalog=names, intent=intent, scope=scope)
    assert check.ok, check.feedback()


def test_repeated_same_source_is_not_a_join(balances):
    catalog = catalog_with_metric(source_tables=(TABLE, TABLE.upper()))
    assert len(rows(balances, catalog=catalog)) == 3


def test_forbidden_formula_or_actual_dimension_source_is_not_silently_dropped():
    assert_error(
        "FORMULA_INVALID",
        stage="symbol_table",
        catalog=catalog_with_metric(formula=f"SUM(current_balnc_cny) FROM {TABLE}"),
    )
    base = semantic_catalog()
    product = replace(base.dimensions["product"], levels={"default": LevelDef("prd_no", table=ORG_TABLE)})
    assert_error(
        "SHARE_SHAPE_UNSUPPORTED", catalog=replace(base, dimensions={**base.dimensions, "product": product})
    )


def test_same_id_display_is_not_duplicated_and_null_name_is_preserved(balances):
    balances.execute(f"UPDATE {TABLE} SET prd_nm = NULL WHERE prd_no = 'LP01'")
    assert rows(balances, intent=branch_context())[0][1] is None
    base = semantic_catalog()
    product = replace(base.dimensions["product"], levels={"default": LevelDef("prd_no", "PRD_NO")})
    catalog = replace(base, dimensions={**base.dimensions, "product": product})
    sql = compile_sql(share_intent(), catalog)
    check = validate_sql(sql, catalog=validation_catalog(), intent=share_intent())
    assert check.ok, check.feedback()
    assert len(rows(balances, catalog=catalog)[0]) == 4


def test_l4_name_validation_still_catches_wrong_physical_column_with_alias_collision():
    statement = compile_intent(
        share_intent(), catalog_with_metric(formula="SUM(nonexistent_amount)"), alias="share_groups"
    )
    result = validate_sql(
        statement.sql(dialect="sqlite"), catalog=validation_catalog(), intent=share_intent()
    )
    assert "UNKNOWN_COLUMN" in result.codes
