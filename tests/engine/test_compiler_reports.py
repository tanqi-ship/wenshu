"""M2.4：直接读取标准报表，固定粒度、月份、口径与权限均不可降级。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import (
    REPORT_COLUMNS,
    REPORT_TABLES,
    TABLE,
    semantic_catalog,
    validation_catalog,
)
from app.engine.semantic.dimension import LevelDef
from app.engine.semantic.models import Filter, Intent, TimeRange
from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog

APRIL, MAY, JUNE = REPORT_TABLES
READ_COLUMNS = frozenset({"stat_month", "prd_no", "prd_nm", "npl_rate"})


def report_intent(**changes) -> Intent:
    return replace(
        Intent(
            metric="不良贷款率",
            time_range=month_end_range(2026, 4),
            dimensions=("产品",),
            source_mode="report",
        ),
        **changes,
    )


def many_months(**changes) -> Intent:
    return report_intent(
        time_range=TimeRange(
            "range",
            start=date(2026, 4, 1),
            end=date(2026, 6, 30),
        ),
        **changes,
    )


def catalog_with_report(**changes):
    base = semantic_catalog()
    return replace(base, reports={"npl_ratio": replace(base.reports["npl_ratio"], **changes)})


def assert_error(code, *, stage="intent", intent=None, catalog=None, scope=None):
    with pytest.raises(CompileError) as error:
        compile_intent(
            intent if intent is not None else report_intent(),
            catalog if catalog is not None else semantic_catalog(),
            scope=scope,
        )
    assert (error.value.stage, error.value.code) == (stage, code)


@pytest.fixture
def report_db():
    db = sqlite3.connect(":memory:")
    for index, table in enumerate(REPORT_TABLES):
        db.execute(f"""CREATE TABLE {table} (
            stat_month TEXT NOT NULL, prd_no TEXT NOT NULL, prd_nm TEXT,
            npl_balance REAL, npl_rate REAL, PRIMARY KEY (stat_month, prd_no)
        )""")
        # 独立抄录 fixture 的业务真值，不执行编译器来生成测试数据。
        month = ("202604", "202605", "202606")[index]
        rates = ((0, 0.140700, 0, 0.30), (0, 0.145, 0, 0.31), (0, 0.15, 0, 0.32))[index]
        balances = (
            (0, 2850000, 4950000, 1100000),
            (0, 2900000, 4900000, 1080000),
            (0, 2950000, 4850000, 1060000),
        )[index]
        db.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?)",
            [
                (month, code, name, balance, rate)
                for code, name, balance, rate in zip(
                    ("LP01", "LP03", "LP05", "LP07"),
                    ("个人住房贷款", "小微企业贷款", "流动资金贷款", "消费贷款"),
                    balances,
                    rates,
                    strict=True,
                )
            ],
        )
    db.execute(f"CREATE TABLE rpt_npl_by_overdue_202604 AS SELECT * FROM {APRIL}")
    db.execute("UPDATE rpt_npl_by_overdue_202604 SET npl_rate = .128300 WHERE prd_no = 'LP03'")
    try:
        yield db
    finally:
        db.close()


def results(db, *, intent=None, catalog=None, scope=None):
    sql = compile_sql(
        intent if intent is not None else report_intent(),
        catalog if catalog is not None else semantic_catalog(),
        scope=scope,
    )
    return db.execute(sql).fetchall()


@pytest.mark.parametrize("mode", ["report", "union"])
def test_single_month_direct_projection_reads_standard_not_overdue(report_db, mode):
    intent = report_intent(source_mode=mode)
    statement = compile_intent(intent, semantic_catalog())
    assert isinstance(statement, exp.Select)
    assert [t.name for t in statement.find_all(exp.Table)] == [APRIL]
    assert [p.alias_or_name for p in statement.expressions] == ["stat_month", "prd_no", "prd_nm", "npl_ratio"]
    assert not any(statement.find_all(exp.AggFunc, exp.Group, exp.Distinct, exp.Mul, exp.Coalesce, exp.Join))
    assert statement.args["where"].sql() == "WHERE t1.stat_month = '202604'"
    rows = results(report_db, intent=intent)
    assert rows == [
        ("202604", "LP01", "个人住房贷款", 0),
        ("202604", "LP03", "小微企业贷款", 0.140700),
        ("202604", "LP05", "流动资金贷款", 0),
        ("202604", "LP07", "消费贷款", 0.30),
    ]
    # AQ-006 旧答案用 MAX/GROUP BY，源表主键成立时与直接读取业务等价。
    legacy = report_db.execute(f"""SELECT stat_month, prd_no, MAX(prd_nm), MAX(npl_rate)
        FROM {APRIL} WHERE stat_month = '202604' GROUP BY stat_month, prd_no
        ORDER BY stat_month, prd_no""").fetchall()
    assert rows == legacy


@pytest.mark.parametrize("mode", ["report", "union"])
def test_three_months_union_all_has_unique_aliases_outer_sort_and_independent_l4(report_db, mode):
    intent = many_months(source_mode=mode)
    statement = compile_intent(intent, semantic_catalog())
    assert isinstance(statement, exp.Union)
    assert len(list(statement.find_all(exp.Select))) == 3
    assert all(node.args["distinct"] is False for node in statement.find_all(exp.Union))
    assert {t.alias for t in statement.find_all(exp.Table)} == {"t1", "t1_2", "t1_3"}
    assert statement.args["order"].sql(dialect="sqlite") == "ORDER BY stat_month, prd_no"
    assert all(s.args.get("order") is None for s in statement.find_all(exp.Select))
    assert not any(statement.find_all(exp.Join, exp.Group, exp.AggFunc))
    check = validate_sql(statement.sql(dialect="sqlite"), catalog=validation_catalog(), intent=intent)
    assert check.ok, check.feedback()
    rows = results(report_db, intent=intent)
    assert len(rows) == 12
    assert [(r[0], r[1]) for r in rows] == sorted((r[0], r[1]) for r in rows)
    assert [r[3] for r in rows if r[1] == "LP03"] == [0.140700, 0.145, 0.15]


def test_alias_argument_is_preserved_for_all_branches():
    statement = compile_intent(many_months(), semantic_catalog(), alias="source")
    assert {t.alias for t in statement.find_all(exp.Table)} == {"source", "source_2", "source_3"}


def test_month_anchors_sort_deduplicate_and_do_not_invent_intermediate_months(report_db):
    intent = report_intent(
        time_range=TimeRange(
            "month",
            points=(
                date(2026, 6, 15),
                date(2026, 4, 1),
                date(2026, 4, 30),
                date(2026, 6, 1),
            ),
        )
    )
    statement = compile_intent(intent, semantic_catalog())
    assert len(list(statement.find_all(exp.Select))) == 2
    rows = results(report_db, intent=intent)
    assert [r[0] for r in rows] == ["202604"] * 4 + ["202606"] * 4


def test_two_anchors_in_one_month_compile_to_select():
    intent = report_intent(time_range=TimeRange("month", points=(date(2026, 4, 1), date(2026, 4, 30))))
    assert isinstance(compile_intent(intent, semantic_catalog()), exp.Select)


def test_each_partition_still_filters_actual_month_column(report_db):
    report_db.execute(f"INSERT INTO {APRIL} VALUES ('202603', 'LP03', '错月', 0, .999)")
    assert len(results(report_db)) == 4
    # 同一物理表可显式登记多个月；仍须按月份分别过滤，绝不盲读全表。
    report_db.execute(f"INSERT INTO {APRIL} SELECT * FROM {MAY}")
    catalog = catalog_with_report(partitions={"202604": APRIL, "202605": APRIL, "202606": JUNE})
    assert results(report_db, intent=many_months(), catalog=catalog) == results(
        report_db, intent=many_months()
    )


def test_null_zero_and_duplicate_values_are_not_coalesced_or_aggregated(report_db):
    report_db.execute(f"UPDATE {APRIL} SET npl_rate = NULL WHERE prd_no = 'LP07'")
    rows = results(report_db)
    assert [r[3] for r in rows] == [0, 0.140700, 0, None]
    assert len(rows) == 4


def test_primary_key_is_data_contract_not_a_compiler_inference(report_db):
    with pytest.raises(sqlite3.IntegrityError):
        report_db.execute(f"INSERT INTO {APRIL} SELECT * FROM {APRIL} WHERE prd_no = 'LP03'")
    report_db.execute(f"CREATE TABLE unconstrained_report AS SELECT * FROM {APRIL}")
    report_db.execute(
        "INSERT INTO unconstrained_report SELECT * FROM unconstrained_report WHERE prd_no = 'LP03'"
    )
    catalog = catalog_with_report(partitions={"202604": "unconstrained_report"})
    assert len(results(report_db, catalog=catalog)) == 5  # 不用 DISTINCT 隐藏真实数据错误。


@pytest.mark.parametrize(
    ("value", "operator", "codes"),
    [
        ("小微企业贷款", "eq", ["LP03"]),
        ("LP03", "eq", ["LP03"]),
        (["小微企业贷款", "LP07"], "in", ["LP03", "LP07"]),
        (["LP03"], "in", ["LP03"]),
        ("LP03", "in", ["LP03"]),
        ([], "in", []),
    ],
)
def test_product_filters_use_shared_exact_code_mapping(report_db, value, operator, codes):
    intent = report_intent(filters=(Filter("贷款产品", value, operator),))
    assert [r[1] for r in results(report_db, intent=intent)] == codes


def test_none_filter_does_not_resolve_or_apply_anything(report_db):
    assert len(results(report_db, intent=report_intent(filters=(Filter("unknown", None, "gte"),)))) == 4


def test_dimension_names_codes_and_synonyms_deduplicate_without_plain_binding():
    base = semantic_catalog()
    # 明细默认口径即使缺失，也不应影响报表自身明确的物理列绑定。
    product = replace(base.dimensions["product"], levels={})
    catalog = replace(base, dimensions={**base.dimensions, "product": product})
    intent = report_intent(dimensions=("产品", "product", "贷款产品"), filters=(Filter("产品", "LP03"),))
    statement = compile_intent(intent, catalog)
    assert len(statement.expressions) == 4
    assert "t1.prd_no = 'LP03'" in statement.sql()


def test_report_specific_columns_do_not_mutate_plain_dimensions():
    base = semantic_catalog()
    catalog = catalog_with_report(
        month_column="rpt_month",
        value_column="ratio_value",
        dimensions={"product": LevelDef("product_id", "product_name")},
    )
    sql = compile_sql(report_intent(filters=(Filter("产品", "LP03"),)), catalog)
    assert "t1.rpt_month AS stat_month" in sql
    assert "t1.product_id = 'LP03'" in sql
    assert "t1.ratio_value AS npl_ratio" in sql
    assert catalog.dimensions == base.dimensions


def test_requested_dimension_order_controls_projection_and_sort():
    catalog = catalog_with_report(
        dimensions={
            "product": LevelDef("prd_no", "prd_nm"),
            "org": LevelDef("org_no", "org_nm"),
        }
    )
    statement = compile_intent(report_intent(dimensions=("机构", "产品")), catalog)
    assert [p.alias_or_name for p in statement.expressions] == [
        "stat_month",
        "org_no",
        "org_nm",
        "prd_no",
        "prd_nm",
        "npl_ratio",
    ]
    assert [c.name for c in statement.args["order"].find_all(exp.Column)] == [
        "stat_month",
        "org_no",
        "prd_no",
    ]


@pytest.mark.parametrize("display", ["", "PRD_NO"])
def test_optional_or_same_as_id_display_is_not_duplicated(display):
    catalog = catalog_with_report(dimensions={"product": LevelDef("prd_no", display)})
    assert len(compile_intent(report_intent(), catalog).expressions) == 3


def test_report_without_dimensions_has_month_and_value_only():
    catalog = catalog_with_report(dimensions={})
    statement = compile_intent(report_intent(dimensions=()), catalog)
    assert [p.alias_or_name for p in statement.expressions] == ["stat_month", "npl_ratio"]


@pytest.mark.parametrize("dimensions", [(), ("机构",), ("产品", "机构"), ("上级机构",)])
def test_report_grain_cannot_be_dropped_or_expanded(dimensions):
    assert_error("REPORT_GRAIN_MISMATCH", intent=report_intent(dimensions=dimensions))


def test_filtering_a_dimension_does_not_allow_dropping_it():
    assert_error(
        "REPORT_GRAIN_MISMATCH", intent=report_intent(dimensions=(), filters=(Filter("产品", "LP03"),))
    )


@pytest.mark.parametrize("dimension", ["机构", "上级机构"])
def test_filter_on_non_report_dimension_cannot_add_a_join(dimension):
    assert_error("REPORT_GRAIN_MISMATCH", intent=report_intent(filters=(Filter(dimension, "B001"),)))


@pytest.mark.parametrize("operator", ["gte", "lte", "between"])
def test_unsupported_filter_operator_is_not_silently_converted_to_equality(operator):
    assert_error(
        "REPORT_FILTER_UNSUPPORTED", intent=report_intent(filters=(Filter("产品", "LP03", operator),))
    )


@pytest.mark.parametrize(
    ("changes", "code", "stage"),
    [
        ({"metric": "unknown"}, "UNKNOWN_METRIC", "symbol_table"),
        ({"metric": None}, "UNKNOWN_METRIC", "intent"),
        ({"metric": "贷款余额"}, "REPORT_SOURCE_MISSING", "symbol_table"),
        ({"dimensions": ("unknown",)}, "UNKNOWN_DIMENSION", "symbol_table"),
        ({"filters": (Filter("unknown", "value"),)}, "UNKNOWN_DIMENSION", "symbol_table"),
        ({"filters": (Filter("产品", "不认识的产品"),)}, "UNKNOWN_CODE", "symbol_table"),
        ({"time_range": None}, "TIME_UNRESOLVED", "intent"),
        ({"comparison": "delta"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "yoy"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "mom"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"comparison": "rate"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"top_n": 2}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"top_n": 2, "top_n_grain": "month"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
        ({"top_n_grain": "month"}, "REPORT_SHAPE_UNSUPPORTED", "intent"),
    ],
)
def test_explicit_report_errors(changes, code, stage):
    assert_error(code, stage=stage, intent=report_intent(**changes))


@pytest.mark.parametrize("missing_month", ["202604", "202605", "202606"])
def test_any_missing_partition_fails_whole_request(missing_month):
    partitions = dict(semantic_catalog().reports["npl_ratio"].partitions)
    partitions.pop(missing_month)
    assert_error(
        "REPORT_PARTITION_MISSING",
        stage="symbol_table",
        intent=many_months(),
        catalog=catalog_with_report(partitions=partitions),
    )


def test_empty_partitions_never_guess_a_table_suffix():
    assert_error("REPORT_PARTITION_MISSING", stage="symbol_table", catalog=catalog_with_report(partitions={}))


@pytest.mark.parametrize("report", [None, "wrong type", {}])
def test_missing_or_malformed_direct_catalog_cannot_fall_back(report):
    catalog = replace(semantic_catalog(), reports={"npl_ratio": report})
    assert_error("REPORT_CONFIG_INVALID", stage="type_check", catalog=catalog)


@pytest.mark.parametrize("reports", [[], None])
def test_direct_catalog_non_mapping_is_config_error(reports):
    assert_error(
        "REPORT_CONFIG_INVALID", stage="type_check", catalog=replace(semantic_catalog(), reports=reports)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"metric_code": "loan_balance"},
        {"value_column": "SUM(npl_rate)"},
        {"partitions": {"202613": APRIL}},
        {"dimensions": {"product": LevelDef("stat_month")}},
        {"dimensions": {"product": LevelDef("prd_no", table=TABLE)}},
    ],
)
def test_direct_catalog_cannot_bypass_report_config_validation(changes):
    assert_error("REPORT_CONFIG_INVALID", stage="type_check", catalog=catalog_with_report(**changes))


@pytest.mark.parametrize("denied", REPORT_TABLES)
def test_any_partition_table_denied_fails_whole_compilation(denied):
    scope = Scope.of([t for t in REPORT_TABLES if t != denied])
    assert_error("TABLE_NOT_IN_SCOPE", stage="scope", intent=many_months(), scope=scope)


@pytest.mark.parametrize("scope", [Scope(), Scope.of([TABLE]), Scope.of(["rpt_npl_by_overdue_202604"])])
def test_explicit_empty_or_wrong_table_scope_never_means_unrestricted(scope):
    assert_error("TABLE_NOT_IN_SCOPE", stage="scope", scope=scope)


def test_only_actual_report_table_access_is_required(report_db):
    scope = Scope.of([APRIL], columns={APRIL: READ_COLUMNS})
    assert len(results(report_db, scope=scope)) == 4


@pytest.mark.parametrize("denied", REPORT_TABLES)
@pytest.mark.parametrize("column", sorted(READ_COLUMNS))
def test_each_month_value_dimension_display_column_is_checked_per_branch(denied, column):
    scope = Scope.of(REPORT_TABLES, columns={denied: READ_COLUMNS - {column}})
    assert_error("COLUMN_NOT_IN_SCOPE", stage="scope", intent=many_months(), scope=scope)


def test_explicit_empty_column_scope_denies_while_missing_whitelist_is_unrestricted():
    scope = Scope.of(REPORT_TABLES, columns={MAY: frozenset()})
    assert_error("COLUMN_NOT_IN_SCOPE", stage="scope", intent=many_months(), scope=scope)
    assert compile_intent(many_months(), semantic_catalog(), scope=Scope.of(REPORT_TABLES)) is not None


def test_row_policy_column_is_also_checked():
    scope = Scope.of(
        [APRIL], columns={APRIL: READ_COLUMNS}, row_policies=(RowPolicy(APRIL, "npl_balance", values=("0",)),)
    )
    assert_error("COLUMN_NOT_IN_SCOPE", stage="scope", scope=scope)


def test_filter_only_and_policy_only_columns_respect_whitelist():
    # 允许报表以零维粒度读取时，策略列仍不能绕过白名单。
    catalog = catalog_with_report(dimensions={})
    scope = Scope.of(
        [APRIL],
        columns={APRIL: frozenset({"stat_month", "npl_rate"})},
        row_policies=(RowPolicy(APRIL, "prd_no", values=("LP03",)),),
    )
    assert_error(
        "COLUMN_NOT_IN_SCOPE",
        stage="scope",
        intent=report_intent(dimensions=()),
        catalog=catalog,
        scope=scope,
    )


def test_each_partition_applies_own_row_policies_in_input_order(report_db):
    scope = Scope.of(
        REPORT_TABLES,
        row_policies=(
            RowPolicy(MAY, "prd_no", values=("LP07",)),
            RowPolicy(APRIL, "npl_balance", values=("2850000",)),
            RowPolicy(APRIL, "prd_no", values=("LP03",)),
            RowPolicy(TABLE, "org_no"),  # 未使用的明细策略不能附加来源，也不能过滤报表。
            RowPolicy(JUNE, "prd_no", values=("LP01", "LP03"), operator="in"),
        ),
    )
    rows = results(report_db, intent=many_months(), scope=scope)
    assert [(r[0], r[1]) for r in rows] == [
        ("202604", "LP03"),
        ("202605", "LP07"),
        ("202606", "LP01"),
        ("202606", "LP03"),
    ]
    statement = compile_intent(many_months(), semantic_catalog(), scope=scope)
    april_where = next(
        s.args["where"].sql() for s in statement.find_all(exp.Select) if s.args["from_"].this.name == APRIL
    )
    assert april_where.index("npl_balance") < april_where.index("prd_no")
    assert {t.name for t in statement.find_all(exp.Table)} == set(REPORT_TABLES)


def test_empty_row_policy_keeps_false_branch_and_other_months_can_return_rows(report_db):
    scope = Scope.of(REPORT_TABLES, row_policies=(RowPolicy(MAY, "prd_no"),))
    statement = compile_intent(many_months(), semantic_catalog(), scope=scope)
    assert len(list(statement.find_all(exp.Select))) == 3
    assert "t1_2.stat_month = '202605' AND 1 = 0" in statement.sql()
    rows = results(report_db, intent=many_months(), scope=scope)
    assert len(rows) == 8
    assert {r[0] for r in rows} == {"202604", "202606"}


def test_false_branch_does_not_hide_table_or_column_permission_failure():
    scope = Scope.of([APRIL, JUNE], row_policies=(RowPolicy(MAY, "prd_no"),))
    assert_error("TABLE_NOT_IN_SCOPE", stage="scope", intent=many_months(), scope=scope)
    scope = Scope.of(REPORT_TABLES, columns={MAY: frozenset()}, row_policies=(RowPolicy(MAY, "prd_no"),))
    assert_error("COLUMN_NOT_IN_SCOPE", stage="scope", intent=many_months(), scope=scope)


def test_legitimate_partition_with_no_data_is_not_a_missing_partition(report_db):
    report_db.execute(f"DELETE FROM {MAY}")
    assert len(results(report_db, intent=many_months())) == 8
    assert len(list(compile_intent(many_months(), semantic_catalog()).find_all(exp.Select))) == 3


def test_independent_l4_cannot_have_early_branch_column_hidden_by_later_alias():
    intent = many_months()
    sql = compile_sql(intent, semantic_catalog())
    names = Catalog.from_mapping(
        {
            "public": {
                APRIL: [c for c in REPORT_COLUMNS if c != "prd_nm"],
                MAY: list(REPORT_COLUMNS),
                JUNE: list(REPORT_COLUMNS),
            }
        }
    )
    check = validate_sql(sql, catalog=names, intent=intent)
    assert not check.ok
    assert "UNKNOWN_COLUMN" in check.codes
    # 独立校验的是首个分支；末分支拥有该列不能覆盖别名目录。
    assert any("t1.prd_nm" in p.message for p in check.problems)


def test_independent_l4_still_rejects_report_join():
    sql = f"SELECT t1.npl_rate FROM {APRIL} AS t1 JOIN {MAY} AS t2 ON t1.prd_no = t2.prd_no"
    check = validate_sql(sql, catalog=validation_catalog(), intent=report_intent())
    assert not check.ok
    assert "JOIN_NOT_ALLOWED" in check.codes


@pytest.mark.parametrize("case_id,expected_count", [("G-015", 4), ("G-016", 12), ("G-017", 2), ("G-018", 2)])
def test_handwritten_report_goldens_execute_with_business_results(report_db, case_id, expected_count):
    from scripts.golden import load_cases

    directory = Path(__file__).resolve().parents[2] / "eval" / "golden"
    case = next(case for case in load_cases(directory) if case.id == case_id)
    handwritten_rows = report_db.execute(case.golden_sql).fetchall()
    compiled_rows = results(report_db, intent=case.intent, scope=case.scope)
    assert handwritten_rows == compiled_rows
    assert len(compiled_rows) == expected_count
    april = next(row for row in compiled_rows if row[0:2] == ("202604", "LP03"))
    assert april[3] == 0.140700
    if case_id in {"G-017", "G-018"}:
        assert [row[0:2] for row in compiled_rows] == [
            ("202604", "LP03"),
            ("202605" if case_id == "G-017" else "202606", "LP07"),
        ]


def test_missing_profile_in_direct_catalog_is_source_error():
    assert_error(
        "REPORT_SOURCE_MISSING", stage="symbol_table", catalog=replace(semantic_catalog(), reports={})
    )


def test_schema_qualified_source_and_case_insensitive_scope_policy(report_db):
    table = f"main.{APRIL}"
    catalog = catalog_with_report(partitions={"202604": table})
    scope = Scope.of(
        [table.upper()],
        columns={table.upper(): READ_COLUMNS},
        row_policies=(RowPolicy(table.upper(), "prd_no", values=("LP03",)),),
    )
    rows = results(report_db, catalog=catalog, scope=scope)
    assert len(rows) == 1
    assert rows[0][1:4] == ("LP03", "小微企业贷款", 0.140700)
    sql = compile_sql(report_intent(), catalog, scope=scope)
    names = Catalog.from_mapping({"main": {APRIL: list(REPORT_COLUMNS)}})
    check = validate_sql(sql, catalog=names, intent=report_intent(), scope=scope)
    assert check.ok, check.feedback()


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "oracle"])
def test_report_ast_can_render_and_roundtrip_in_configured_dialects(dialect):
    import sqlglot

    sql = compile_sql(many_months(), semantic_catalog(), dialect=dialect)
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    assert isinstance(parsed, exp.Union)
    assert {table.name for table in parsed.find_all(exp.Table)} == set(REPORT_TABLES)
    assert len(list(parsed.find_all(exp.Select))) == 3
    # 这里只保证 AST/文本方言，不宣称已经在真实 Oracle/PostgreSQL 执行验收。
