"""M2.3：受限双表关联的结构、业务结果及确定性拒绝边界。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import ORG_TABLE, TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef, LevelDef
from app.engine.semantic.models import Filter, Intent, RelationDef
from app.engine.validation import ValidationPolicy, validate_sql


def join_intent(**changes: object) -> Intent:
    return replace(
        Intent(metric="贷款余额", time_range=month_end_range(2026, 4), dimensions=("上级机构",)), **changes
    )


def catalog_with(**changes: object) -> SemanticCatalog:
    return replace(semantic_catalog(), **changes)


def relation(**changes: object) -> RelationDef:
    return replace(semantic_catalog().relations[0], **changes)


def assert_compile_error(
    code: str,
    *,
    stage: str = "type_check",
    intent: Intent | None = None,
    catalog: SemanticCatalog | None = None,
    **kwargs,
) -> None:
    with pytest.raises(CompileError) as error:
        compile_intent(
            intent or join_intent(), catalog if catalog is not None else semantic_catalog(), **kwargs
        )
    assert (error.value.stage, error.value.code) == (stage, code)


def test_inner_join_binds_dimension_and_metric_to_different_sources() -> None:
    statement = compile_intent(join_intent(), semantic_catalog())
    assert len(list(statement.find_all(exp.Select))) == 1
    assert len(list(statement.find_all(exp.Table))) == 2
    join = statement.args["joins"][0]
    assert join.args["kind"] == "INNER"
    assert join.args["on"].sql() == "t1.org_no = t2.org_no"
    assert statement.args["group"].expressions[0].sql() == "t2.parent_org_no"
    assert statement.expressions[1].this.this.sql() == "t1.current_balnc_cny"
    assert statement.args["where"].this.sql() == "t1.etl_dt = '2026-04-30'"
    result = validate_sql(
        statement.sql(dialect="sqlite"), catalog=validation_catalog(), intent=join_intent(), dialect="sqlite"
    )
    assert result.ok, result.feedback()


def test_adding_relation_to_catalog_does_not_join_unused_table() -> None:
    intent = join_intent(dimensions=("机构",))
    statement = compile_intent(intent, semantic_catalog())
    assert not statement.args.get("joins")
    assert len(list(statement.find_all(exp.Table))) == 1


def test_filter_only_dimension_introduces_join_but_none_filter_does_not() -> None:
    intent = join_intent(dimensions=(), filters=(Filter("上级机构", "F001"),))
    statement = compile_intent(intent, semantic_catalog())
    assert statement.args.get("joins")
    assert "t2.parent_org_no = 'F001'" in statement.args["where"].sql()
    no_value = replace(intent, filters=(Filter("上级机构", None),))
    assert not compile_intent(no_value, semantic_catalog()).args.get("joins")


def test_relation_order_does_not_affect_result() -> None:
    unrelated = RelationDef("elsewhere", "a", "another", "b")
    a = catalog_with(relations=(unrelated, relation()))
    b = catalog_with(relations=(relation(), unrelated))
    assert compile_sql(join_intent(), a) == compile_sql(join_intent(), b)


def test_inner_can_reverse_declared_one_to_many_into_many_to_one() -> None:
    reverse = RelationDef(ORG_TABLE, "org_no", TABLE, "org_no", cardinality="one_to_many")
    assert compile_sql(join_intent(), catalog_with(relations=(reverse,))) == compile_sql(
        join_intent(), semantic_catalog()
    )


def test_one_to_one_can_reverse_inner_relation() -> None:
    reverse = RelationDef(ORG_TABLE, "org_no", TABLE, "org_no", cardinality="one_to_one")
    assert compile_intent(join_intent(), catalog_with(relations=(reverse,))).args.get("joins")


@pytest.mark.parametrize("alias", ["loans", "t2", "T2", "t3"])
def test_custom_primary_alias_cannot_collide_with_join_alias(alias: str) -> None:
    statement = compile_intent(join_intent(), semantic_catalog(), alias=alias)
    tables = list(statement.find_all(exp.Table))
    assert len({t.alias.casefold() for t in tables}) == 2
    assert statement.args["joins"][0].args["on"].this.table == alias


def test_metric_source_tables_are_not_silently_truncated() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], source_tables=(TABLE, ORG_TABLE))
    catalog = replace(base, metrics={metric.code: metric})
    assert compile_intent(join_intent(dimensions=()), catalog).args.get("joins")


def test_duplicate_source_tables_are_case_insensitively_deduplicated() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], source_tables=(TABLE.upper(), TABLE, ORG_TABLE.upper()))
    assert compile_intent(join_intent(), replace(base, metrics={metric.code: metric})).args.get("joins")


@pytest.mark.parametrize(
    "formula",
    [
        "SUM(current_balnc_cny)",
        "SUM({tbl}.current_balnc_cny)",
        "SUM({t}.current_balnc_cny)",
        "SUM(qa_loan_acc_detail.current_balnc_cny)",
        "SUM(CASE WHEN {tbl}.org_no = 'B001' THEN current_balnc_cny ELSE 0 END)",
    ],
)
def test_primary_formula_variants_are_bound(formula: str) -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], formula=formula)
    statement = compile_intent(join_intent(), replace(base, metrics={metric.code: metric}), alias="loans")
    metric_projection = statement.expressions[-1]
    assert {c.table for c in metric_projection.find_all(exp.Column)} == {"loans"}


@pytest.mark.parametrize(
    "formula",
    [
        "SUM(dim_org.org_level)",
        "SUM(t2.org_level)",
        "SUM(unknown.amount)",
        "(SELECT SUM(current_balnc_cny) FROM qa_loan_acc_detail)",
        "SUM(current_balnc_cny) OVER ()",
    ],
)
def test_non_primary_or_nested_formula_is_explicitly_rejected(formula: str) -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], formula=formula)
    assert_compile_error("JOIN_FORMULA_UNSUPPORTED", catalog=replace(base, metrics={metric.code: metric}))


def test_schema_qualified_table_and_formula_bind_without_guessing_bare_names() -> None:
    base = semantic_catalog()
    metric = replace(
        base.metrics["loan_balance"], source_tables=("bank.loans",), formula="SUM(bank.loans.amount)"
    )
    dim = DimensionDef(
        "parent", "上级机构", levels={"default": LevelDef("parent_org_no", table="bank.organizations")}
    )
    catalog = SemanticCatalog.build(
        metrics=[metric],
        dimensions=[dim],
        relations=[
            RelationDef("bank.loans", "org_no", "bank.organizations", "org_no", cardinality="many_to_one")
        ],
    )
    scope = Scope.of(["bank.loans", "bank.organizations"])
    statement = compile_intent(join_intent(), catalog, scope=scope)
    assert statement.expressions[-1].this.this.sql() == "t1.amount"
    assert "bank.loans AS t1" in statement.sql()
    assert "bank.organizations AS t2" in statement.sql()
    bare = replace(dim, levels={"default": LevelDef("parent_org_no", table="organizations")})
    assert_compile_error(
        "JOIN_RELATION_MISSING", stage="symbol_table", catalog=replace(catalog, dimensions={bare.code: bare})
    )


def test_same_named_columns_and_names_use_their_own_table_alias() -> None:
    base = semantic_catalog()
    external = DimensionDef(
        "directory_org", "目录机构", levels={"only": LevelDef("org_no", "org_nm", table=ORG_TABLE)}
    )
    catalog = replace(base, dimensions={**base.dimensions, external.code: external})
    statement = compile_intent(join_intent(dimensions=("目录机构",)), catalog)
    assert statement.args["group"].expressions[0].sql() == "t2.org_no"
    assert statement.expressions[1].this.sql() == "MAX(t2.org_nm)"
    assert statement.expressions[-1].this.this.sql() == "t1.current_balnc_cny"
    assert_compile_error(
        "JOIN_OUTPUT_COLLISION", intent=join_intent(dimensions=("机构", "目录机构")), catalog=catalog
    )


@pytest.mark.parametrize(
    ("relations", "code", "stage"),
    [
        ((), "JOIN_RELATION_MISSING", "symbol_table"),
        ((relation(), relation()), "JOIN_RELATION_AMBIGUOUS", "type_check"),
        ((relation(), relation(left_column="accounting_org_no")), "JOIN_RELATION_AMBIGUOUS", "type_check"),
        ((relation(cardinality="unknown"),), "JOIN_CARDINALITY_UNSAFE", "type_check"),
        ((relation(cardinality="one_to_many"),), "JOIN_CARDINALITY_UNSAFE", "type_check"),
        ((relation(cardinality="many_to_many"),), "JOIN_CARDINALITY_UNSAFE", "type_check"),
        ((relation(join_type="RIGHT"),), "JOIN_TYPE_UNSUPPORTED", "type_check"),
        ((relation(join_type="FULL"),), "JOIN_TYPE_UNSUPPORTED", "type_check"),
        ((relation(join_type="CROSS"),), "JOIN_TYPE_UNSUPPORTED", "type_check"),
        ((relation(left_column="org_no,accounting_org_no"),), "JOIN_TYPE_UNSUPPORTED", "type_check"),
        ((relation(right_column=""),), "JOIN_TYPE_UNSUPPORTED", "type_check"),
        (
            (RelationDef(ORG_TABLE, "org_no", TABLE, "org_no", join_type="LEFT", cardinality="one_to_many"),),
            "JOIN_TYPE_UNSUPPORTED",
            "type_check",
        ),
        (
            (RelationDef(ORG_TABLE, "org_no", TABLE, "org_no", cardinality="many_to_one"),),
            "JOIN_CARDINALITY_UNSAFE",
            "type_check",
        ),
    ],
)
def test_invalid_relations_fail_with_stable_codes(relations, code: str, stage: str) -> None:
    assert_compile_error(code, stage=stage, catalog=catalog_with(relations=relations))


def test_three_sources_are_rejected_from_metrics_or_dimensions() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], source_tables=(TABLE, ORG_TABLE, "third_table"))
    assert_compile_error("JOIN_TABLE_LIMIT", catalog=replace(base, metrics={metric.code: metric}))
    dim = DimensionDef("third", "第三维度", levels={"only": LevelDef("id", table="third_table")})
    assert_compile_error(
        "JOIN_TABLE_LIMIT",
        intent=join_intent(dimensions=("上级机构", "第三维度")),
        catalog=replace(base, dimensions={**base.dimensions, dim.code: dim}),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"comparison": "delta"},
        {"comparison": "yoy"},
        {"top_n": 1, "top_n_grain": "month"},
        {"top_n_grain": "month"},
        {"source_mode": "report"},
        {"source_mode": "union"},
    ],
)
def test_join_shape_combinations_are_rejected(changes) -> None:
    assert_compile_error("JOIN_SHAPE_UNSUPPORTED", stage="intent", intent=join_intent(**changes))


@pytest.mark.parametrize("operator", ["gte", "lte", "between", "like"])
def test_join_filter_never_silently_falls_back_to_equality(operator: str) -> None:
    intent = join_intent(filters=(Filter("上级机构", "F001", operator=operator),))
    assert_compile_error("JOIN_FILTER_UNSUPPORTED", stage="intent", intent=intent)


def test_missing_metric_source_is_still_unknown_source() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], source_tables=())
    assert_compile_error(
        "UNKNOWN_SOURCE", stage="symbol_table", catalog=replace(base, metrics={metric.code: metric})
    )


@pytest.fixture
def bank():
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            f"CREATE TABLE {TABLE} (etl_dt TEXT, org_no TEXT, org_nm TEXT, prd_no TEXT, prd_nm TEXT, current_balnc_cny REAL, fiv_lev_cla_flag TEXT)"
        )
        connection.execute(
            f"CREATE TABLE {ORG_TABLE} (org_no TEXT PRIMARY KEY NOT NULL, org_nm TEXT, parent_org_no TEXT, org_level INTEGER, org_type TEXT)"
        )
        connection.executemany(
            f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("2026-04-30", "B001", "名称甲", "LP01", "产品甲", 60, "30"),
                ("2026-04-30", "B001", "名称甲 ", "LP01", "产品甲", 40, "10"),
                ("2026-04-30", "B002", "名称乙", "LP03", "产品乙", 200, "30"),
                ("2026-04-30", "B003", "名称丙", "LP01", "产品甲", 50, "10"),
                ("2026-04-30", "B404", "缺失机构", "LP01", "产品甲", 25, "30"),
                ("2026-03-31", "B001", "名称甲", "LP01", "产品甲", 99999, "30"),
            ],
        )
        connection.executemany(
            f"INSERT INTO {ORG_TABLE} VALUES (?, ?, ?, ?, ?)",
            [
                ("B001", "目录甲", "F001", 3, "financial"),
                ("B002", "目录乙", "F001", 3, "financial"),
                ("B003", "目录丙", "F002", 3, "customer"),
                ("B999", "无贷款机构", "F003", 3, "financial"),
            ],
        )
        yield connection


def results(
    bank, *, intent: Intent | None = None, catalog: SemanticCatalog | None = None, scope: Scope | None = None
):
    sql = compile_sql(
        intent or join_intent(), catalog if catalog is not None else semantic_catalog(), scope=scope
    )
    return bank.execute(sql).fetchall()


def test_inner_sums_multiple_loans_once_and_drops_unmatched_organizations(bank) -> None:
    assert dict(results(bank)) == {"F001": 300, "F002": 50}


def test_left_keeps_unmatched_loan_as_null_group(bank) -> None:
    catalog = catalog_with(relations=(relation(join_type="LEFT"),))
    assert dict(results(bank, catalog=catalog)) == {"F001": 300, "F002": 50, None: 25}


def test_left_right_side_business_filter_is_intentionally_a_where_predicate(bank) -> None:
    catalog = catalog_with(relations=(relation(join_type="LEFT"),))
    intent = join_intent(filters=(Filter("上级机构", "F001"),))
    assert dict(results(bank, catalog=catalog, intent=intent)) == {"F001": 300}
    statement = compile_intent(intent, catalog)
    assert statement.args["joins"][0].args["on"].sql() == "t1.org_no = t2.org_no"
    assert "t2.parent_org_no = 'F001'" in statement.args["where"].sql()


def test_eq_and_in_business_filters_share_code_mapping(bank) -> None:
    base = semantic_catalog()
    catalog = replace(base, code_map={**base.code_map, "parent_org": {"分行甲": "F001", "分行乙": "F002"}})
    intent = join_intent(filters=(Filter("上级机构", "分行甲"),))
    assert dict(results(bank, intent=intent, catalog=catalog)) == {"F001": 300}
    intent = replace(intent, filters=(Filter("上级机构", ("分行甲", "F002"), operator="in"),))
    assert dict(results(bank, intent=intent, catalog=catalog)) == {"F001": 300, "F002": 50}
    assert_compile_error(
        "UNKNOWN_CODE",
        stage="symbol_table",
        intent=replace(intent, filters=(Filter("上级机构", "不存在"),)),
        catalog=catalog,
    )


def test_conditional_and_ratio_metric_keep_formula_semantics(bank) -> None:
    assert dict(results(bank, intent=join_intent(metric="不良贷款余额"))) == {"F001": 260, "F002": 0}
    ratios = dict(results(bank, intent=join_intent(metric="不良贷款率")))
    assert ratios["F001"] == pytest.approx(260 / 300)
    assert ratios["F002"] == 0


def test_multi_dimension_grouping_and_display_names_remain_separate(bank) -> None:
    intent = join_intent(dimensions=("上级机构", "产品"))
    assert set(results(bank, intent=intent)) == {
        ("F001", "LP01", "产品甲", 100),
        ("F001", "LP03", "产品乙", 200),
        ("F002", "LP01", "产品甲", 50),
    }


def test_dimension_primary_key_rejects_real_duplicate_rows(bank) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        bank.execute(f"INSERT INTO {ORG_TABLE} VALUES ('B001', '重复', 'F001', 3, 'financial')")


def test_unconstrained_duplicate_key_demonstrates_why_metadata_is_not_a_data_check(bank) -> None:
    bank.execute(f"CREATE TABLE bad_org AS SELECT * FROM {ORG_TABLE}")
    bank.execute(f"INSERT INTO bad_org SELECT * FROM {ORG_TABLE} WHERE org_no = 'B001'")
    base = semantic_catalog()
    dim = replace(
        base.dimensions["parent_org"], levels={"default": LevelDef("parent_org_no", table="bad_org")}
    )
    catalog = replace(
        base, dimensions={**base.dimensions, dim.code: dim}, relations=(relation(right_table="bad_org"),)
    )
    # 编译器信任 many_to_one 声明，不连接数据库，不能发现配置与数据不符。
    assert dict(results(bank, catalog=catalog))["F001"] == 400


def test_row_policies_apply_to_their_own_table_in_original_order(bank) -> None:
    scope = Scope.of(
        [TABLE, ORG_TABLE],
        row_policies=(
            RowPolicy(ORG_TABLE, "parent_org_no", values=("F001",)),
            RowPolicy(TABLE, "org_no", values=("B001",)),
        ),
    )
    assert dict(results(bank, scope=scope)) == {"F001": 100}
    assert dict(results(bank, scope=scope, intent=join_intent(filters=(Filter("产品", "LP01"),)))) == {
        "F001": 100
    }
    sql = compile_sql(join_intent(filters=(Filter("产品", "LP01"),)), semantic_catalog(), scope=scope)
    where = sql.split("WHERE", 1)[1]
    assert (
        where.index("etl_dt")
        < where.index("prd_no")
        < where.index("t2.parent_org_no")
        < where.index("t1.org_no")
    )


def test_left_right_policy_must_not_reclassify_denied_balance_into_null_group(bank) -> None:
    catalog = catalog_with(relations=(relation(join_type="LEFT"),))
    scope = Scope.of(
        [TABLE, ORG_TABLE], row_policies=(RowPolicy(ORG_TABLE, "parent_org_no", values=("F001",)),)
    )
    assert dict(results(bank, catalog=catalog, scope=scope)) == {"F001": 300}


@pytest.mark.parametrize("table,column", [(TABLE, "org_no"), (ORG_TABLE, "org_no")])
def test_empty_row_policy_denies_all_even_for_left_join(bank, table: str, column: str) -> None:
    catalog = catalog_with(relations=(relation(join_type="LEFT"),))
    scope = Scope.of([TABLE, ORG_TABLE], row_policies=(RowPolicy(table, column),))
    assert results(bank, catalog=catalog, scope=scope) == []


def test_policies_for_unused_tables_neither_join_nor_filter(bank) -> None:
    scope = Scope.of([TABLE], row_policies=(RowPolicy(ORG_TABLE, "org_no"),))
    intent = join_intent(dimensions=())
    assert results(bank, scope=scope, intent=intent) == [(375,)]
    assert "dim_org" not in compile_sql(intent, semantic_catalog(), scope=scope)


@pytest.mark.parametrize(
    ("tables", "code"),
    [([], "METRIC_OUT_OF_SCOPE"), ([ORG_TABLE], "METRIC_OUT_OF_SCOPE"), ([TABLE], "TABLE_NOT_IN_SCOPE")],
)
def test_every_participating_table_requires_access(tables, code: str) -> None:
    assert_compile_error(code, stage="scope", scope=Scope.of(tables))


@pytest.mark.parametrize(
    "table,column",
    [
        (TABLE, "org_no"),
        (TABLE, "etl_dt"),
        (TABLE, "current_balnc_cny"),
        (ORG_TABLE, "org_no"),
        (ORG_TABLE, "parent_org_no"),
    ],
)
def test_column_scope_covers_join_time_metric_and_dimension(table: str, column: str) -> None:
    names = {TABLE: {"org_no", "etl_dt", "current_balnc_cny"}, ORG_TABLE: {"org_no", "parent_org_no"}}
    names[table].remove(column)
    scope = Scope.of([TABLE, ORG_TABLE], columns={key: frozenset(value) for key, value in names.items()})
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", scope=scope)


def test_column_scope_distinguishes_unspecified_table_from_empty_whitelist() -> None:
    allowed = frozenset({"org_no", "etl_dt", "current_balnc_cny"})
    scope = Scope.of([TABLE, ORG_TABLE], columns={TABLE: allowed})
    assert compile_intent(join_intent(), semantic_catalog(), scope=scope).args.get("joins")
    blocked = replace(scope, columns={**scope.columns, ORG_TABLE: frozenset()})
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", scope=blocked)


def test_column_scope_includes_policy_filter_and_display_columns() -> None:
    base = semantic_catalog()
    dim = replace(
        base.dimensions["parent_org"],
        levels={"default": LevelDef("parent_org_no", "org_nm", table=ORG_TABLE)},
    )
    catalog = replace(base, dimensions={**base.dimensions, dim.code: dim})
    columns = {ORG_TABLE: frozenset({"org_no", "parent_org_no"})}
    scope = Scope.of([TABLE, ORG_TABLE], columns=columns)
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", catalog=catalog, scope=scope)
    intent = join_intent(dimensions=(), filters=(Filter("上级机构", "F001"),))
    assert_compile_error(
        "COLUMN_NOT_IN_SCOPE",
        stage="scope",
        intent=intent,
        scope=Scope.of([TABLE, ORG_TABLE], columns={ORG_TABLE: frozenset({"org_no"})}),
    )
    scope = Scope.of(
        [TABLE, ORG_TABLE],
        columns=columns,
        row_policies=(RowPolicy(ORG_TABLE, "org_type", values=("financial",)),),
    )
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", scope=scope)


def test_single_table_also_enforces_explicit_scope_and_column_whitelist() -> None:
    intent = join_intent(dimensions=())
    assert_compile_error("METRIC_OUT_OF_SCOPE", stage="scope", intent=intent, scope=Scope())
    assert_compile_error(
        "COLUMN_NOT_IN_SCOPE",
        stage="scope",
        intent=intent,
        scope=Scope.of([TABLE], columns={TABLE: frozenset({"etl_dt"})}),
    )
    scope = Scope.of([TABLE], columns={TABLE: frozenset({"ETL_DT", "CURRENT_BALNC_CNY"})})
    assert compile_intent(intent, semantic_catalog(), scope=scope)


def test_column_scope_is_applied_before_topn_wrapping() -> None:
    intent = join_intent(dimensions=("机构",), top_n=1, top_n_grain="month")
    scope = Scope.of([TABLE], columns={TABLE: frozenset({"etl_dt", "org_no", "org_nm", "current_balnc_cny"})})
    assert compile_intent(intent, semantic_catalog(), scope=scope).find(exp.Window)
    blocked = replace(scope, columns={TABLE: frozenset({"etl_dt", "org_no", "current_balnc_cny"})})
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", intent=intent, scope=blocked)


def test_custom_snapshot_column_is_on_primary_table_only() -> None:
    statement = compile_intent(
        join_intent(), semantic_catalog(), policy=ValidationPolicy(snapshot_column="snapshot_dt")
    )
    assert statement.args["where"].this.this.sql() == "t1.snapshot_dt"


@pytest.mark.parametrize(
    "formula",
    [
        "SUM(current_balnc_cny) FROM other_table",
        "SUM(current_balnc_cny) WHERE org_no = 'B001'",
        "SUM(current_balnc_cny); SELECT 1",
        "SUM(current_balnc_cny), SUM(current_balnc_cny)",
    ],
)
def test_formula_must_be_one_expression_not_a_query_fragment(formula: str) -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], formula=formula)
    assert_compile_error(
        "FORMULA_INVALID", stage="symbol_table", catalog=replace(base, metrics={metric.code: metric})
    )


def test_single_snapshot_schema_alias_and_column_permissions_together() -> None:
    base = semantic_catalog()
    metric = replace(
        base.metrics["loan_balance"], source_tables=("bank.loans",), formula="SUM(bank.loans.amount)"
    )
    dim = DimensionDef(
        "parent", "上级机构", levels={"only": LevelDef("parent_org_no", table="bank.organizations")}
    )
    catalog = SemanticCatalog.build(
        metrics=[metric],
        dimensions=[dim],
        relations=[
            RelationDef("bank.loans", "org_no", "bank.organizations", "org_no", cardinality="many_to_one")
        ],
    )
    scope = Scope.of(
        ["bank.loans", "bank.organizations"],
        columns={
            "bank.loans": frozenset({"etl_dt", "org_no", "amount"}),
            "bank.organizations": frozenset({"org_no", "parent_org_no"}),
        },
        row_policies=(RowPolicy("bank.organizations", "parent_org_no", values=("F001",)),),
    )
    statement = compile_intent(join_intent(), catalog, scope=scope, alias="loans")
    assert statement.expressions[-1].this.this.sql() == "loans.amount"
    assert "t2.parent_org_no = 'F001'" in statement.args["where"].sql()


def test_no_scope_stays_unrestricted_but_l4_denies_explicit_empty_scope() -> None:
    sql = compile_sql(join_intent(), semantic_catalog())
    assert validate_sql(sql, catalog=validation_catalog()).ok
    result = validate_sql(sql, catalog=validation_catalog(), scope=Scope())
    assert not result.ok and "TABLE_NOT_IN_SCOPE" in result.codes


@pytest.mark.parametrize("formula", ["COUNT(*)", "COUNT({tbl}.*)"])
def test_count_star_cannot_bypass_explicit_column_restrictions(formula: str) -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], formula=formula)
    catalog = replace(base, metrics={metric.code: metric})
    scope = Scope.of([TABLE, ORG_TABLE], columns={TABLE: frozenset({"etl_dt", "org_no"})})
    assert_compile_error("COLUMN_NOT_IN_SCOPE", stage="scope", catalog=catalog, scope=scope)


def test_metric_output_cannot_shadow_join_dimension() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], code="parent_org_no")
    assert_compile_error("JOIN_OUTPUT_COLLISION", catalog=replace(base, metrics={metric.code: metric}))


def test_explicit_same_primary_source_on_dimension_is_still_single_table() -> None:
    base = semantic_catalog()
    dim = replace(
        base.dimensions["org"],
        levels={"financial": LevelDef("org_no", "org_nm", table=TABLE)},
        default_level="financial",
    )
    catalog = replace(base, dimensions={**base.dimensions, dim.code: dim})
    statement = compile_intent(join_intent(dimensions=("机构",)), catalog)
    assert not statement.args.get("joins")


def test_alias_dimensions_are_deduplicated_by_semantic_code() -> None:
    statement = compile_intent(join_intent(dimensions=("上级机构", "上级行")), semantic_catalog())
    assert len(statement.expressions) == 2


def test_missing_join_dimension_or_level_keeps_original_error_classification() -> None:
    assert_compile_error(
        "UNKNOWN_DIMENSION", stage="symbol_table", intent=join_intent(dimensions=("不存在的维度",))
    )
    dim = DimensionDef(
        "ambiguous", "歧义维度", levels={"a": LevelDef("a"), "b": LevelDef("b", table=ORG_TABLE)}
    )
    base = semantic_catalog()
    assert_compile_error(
        "DIMENSION_LEVEL_UNRESOLVED",
        stage="intent",
        intent=join_intent(dimensions=("歧义维度",)),
        catalog=replace(base, dimensions={**base.dimensions, dim.code: dim}),
    )


def test_primary_alias_cannot_disguise_a_secondary_physical_table_reference() -> None:
    base = semantic_catalog()
    metric = replace(base.metrics["loan_balance"], formula="SUM(dim_org.org_no)")
    assert_compile_error(
        "JOIN_FORMULA_UNSUPPORTED", catalog=replace(base, metrics={metric.code: metric}), alias="dim_org"
    )


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"cardinality": None}, "JOIN_CARDINALITY_UNSAFE"),
        ({"cardinality": ["many_to_one"]}, "JOIN_CARDINALITY_UNSAFE"),
        ({"join_type": ["INNER"]}, "JOIN_TYPE_UNSUPPORTED"),
        ({"left_column": ["org_no"]}, "JOIN_TYPE_UNSUPPORTED"),
    ],
)
def test_malformed_relation_fields_fail_explicitly_not_with_type_error(changes, code: str) -> None:
    assert_compile_error(code, catalog=catalog_with(relations=(relation(**changes),)))
