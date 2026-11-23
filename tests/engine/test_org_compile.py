"""机构业务过滤的稳定拒绝、跨形状复用及独立结果检查。"""

import sqlite3
from dataclasses import replace

import pytest

from app.engine.compiler import CompileError, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog
from app.engine.semantic.dimension import LevelDef
from app.engine.semantic.models import Filter, Intent, ShareSpec, TimeRange


def intent(value="渝北", **changes):
    return replace(
        Intent(metric="贷款余额", time_range=month_end_range(2026, 4), filters=(Filter("机构", value),)),
        **changes,
    )


def assert_error(code, *, query=None, catalog=None, stage="symbol_table"):
    with pytest.raises(CompileError) as error:
        compile_sql(
            query if query is not None else intent(), catalog if catalog is not None else semantic_catalog()
        )
    assert (error.value.stage, error.value.code) == (stage, code)


@pytest.mark.parametrize("value", ["渝北支行", "渝北", " B001 ", "B001"])
def test_registered_org_always_exact(value):
    assert "t1.org_no = 'B001'" in compile_sql(intent(value), semantic_catalog())


@pytest.mark.parametrize("value", ["渝北%", "B0019", "b001", "渝 北", 1, True, ["B001"], {}, ""])
def test_unknown_value_is_not_silent_zero(value):
    assert_error("UNKNOWN_CODE", query=intent(value))


@pytest.mark.parametrize(
    "mapping,code,stage",
    [
        ({}, "EXACT_VALUE_MAP_MISSING", "type_check"),
        ({"org": {}}, "EXACT_VALUE_MAP_MISSING", "type_check"),
        ({"org": None}, "EXACT_VALUE_MAP_INVALID", "type_check"),
        ({"org": {"渝北": " B001"}}, "EXACT_VALUE_MAP_INVALID", "type_check"),
        ({"org": {"渝北": "B001", "B001": "B002"}}, "AMBIGUOUS_CODE", "symbol_table"),
    ],
)
def test_direct_catalog_replace_still_validated(mapping, code, stage):
    assert_error(code, catalog=replace(semantic_catalog(), code_map=mapping), stage=stage)


@pytest.mark.parametrize("operator", ["gte", "lte", "between", "like", "eq ", "ne"])
def test_non_exact_operator_is_never_downgraded(operator):
    assert_error(
        "EXACT_FILTER_UNSUPPORTED", query=intent(filters=(Filter("机构", "B001", operator),)), stage="intent"
    )


def test_invalid_value_kind_cannot_restore_legacy_fallback():
    base = semantic_catalog()
    dims = {**base.dimensions, "org": replace(base.dimensions["org"], value_kind="bad")}
    assert_error("EXACT_VALUE_MAP_INVALID", catalog=replace(base, dimensions=dims), stage="type_check")


@pytest.mark.parametrize("value", [["B001", "unknown"], ["B001", None], ["B001", 1], [["B001"]]])
def test_in_never_drops_failed_items(value):
    assert_error("UNKNOWN_CODE", query=intent(filters=(Filter("机构", value, "in"),)))


def test_in_accepts_full_alias_and_code_in_original_order():
    sql = compile_sql(intent(filters=(Filter("机构", ["渝北支行", "B002"], "in"),)), semantic_catalog())
    assert "t1.org_no IN ('B001', 'B002')" in sql


@pytest.mark.parametrize("value,operator", [(None, "eq"), ([], "in")])
def test_no_value_does_not_require_map(value, operator):
    sql = compile_sql(
        intent(filters=(Filter("机构", value, operator),)), replace(semantic_catalog(), code_map={})
    )
    assert ("1 = 0" in sql) == (operator == "in")


def test_scope_is_not_retranslated_or_limited_to_business_registry():
    base = semantic_catalog()
    base = replace(base, code_map={"org": {"渝北": "B001"}})
    scope = Scope.of([TABLE], row_policies=(RowPolicy(TABLE, "org_no", values=("B001", "EXTERNAL")),))
    sql = compile_sql(intent(), base, scope=scope)
    assert "t1.org_no = 'B001'" in sql
    assert "t1.org_no IN ('B001', 'EXTERNAL')" in sql


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {
            "comparison": "delta",
            "time_range": TimeRange(
                "month_end", points=(*month_end_range(2026, 3).points, *month_end_range(2026, 4).points)
            ),
        },
        {"dimensions": ("产品",), "top_n": 2, "top_n_grain": "month"},
        {"dimensions": ("产品",), "share": ShareSpec("产品")},
        {"dimensions": ("上级机构",)},
    ],
)
def test_all_existing_detail_shapes_use_same_exact_resolution(changes):
    assert "t1.org_no = 'B001'" in compile_sql(intent(**changes), semantic_catalog())


@pytest.mark.parametrize("mode", ["report", "union"])
def test_report_uses_registered_strict_dimension_when_grain_declared(mode):
    base = semantic_catalog()
    report = replace(base.reports["npl_ratio"], dimensions={"org": LevelDef("org_no", "org_nm")})
    query = intent(metric="不良贷款率", source_mode=mode, dimensions=("机构",))
    assert "t1.org_no = 'B001'" in compile_sql(query, replace(base, reports={"npl_ratio": report}))


@pytest.mark.parametrize("mode", ["plain", "report", "union"])
def test_top_level_org_never_silently_ignored(mode):
    query = intent(org="渝北", source_mode=mode)
    assert_error("ORG_CONTEXT_UNRESOLVED", query=query, stage="intent")


def test_share_keeps_its_existing_context_error():
    query = intent(org="渝北", dimensions=("产品",), share=ShareSpec("产品"))
    assert_error("SHARE_CONTEXT_UNRESOLVED", query=query, stage="intent")


def test_independent_sqlite_data_excludes_similar_codes_and_descendants():
    with sqlite3.connect(":memory:") as db:
        db.execute(f"CREATE TABLE {TABLE} (etl_dt TEXT, org_no TEXT, org_nm TEXT, current_balnc_cny INTEGER)")
        db.executemany(
            f"INSERT INTO {TABLE} VALUES ('2026-04-30', ?, ?, ?)",
            [
                ("B001", "渝北支行", 100),
                ("B0019", "渝北支行营业部", 200),
                ("O001", "渝北龙溪分理处", 300),
            ],
        )
        assert db.execute(compile_sql(intent(), semantic_catalog())).fetchone() == (100,)
        assert db.execute(
            f"SELECT SUM(current_balnc_cny) FROM {TABLE} WHERE org_no LIKE 'B001%'"
        ).fetchone() == (300,)
        assert db.execute(
            f"SELECT SUM(current_balnc_cny) FROM {TABLE} WHERE org_nm LIKE '渝北%'"
        ).fetchone() == (600,)


def test_financial_and_management_binding_unchanged():
    base = semantic_catalog()
    org = replace(base.dimensions["org"], default_level="customer_management")
    sql = compile_sql(intent(), replace(base, dimensions={**base.dimensions, "org": org}))
    assert "t1.accounting_org_no = 'B001'" in sql


@pytest.mark.parametrize(
    "case_id,expected",
    [
        ("G-023", [(1000,)]),
        ("G-024", [("B001", "渝北支行", 1000), ("B002", "江北支行", 900)]),
        ("G-025", [("B001", 50)]),
        ("G-026", [("LP03", "小微企业贷款", 300, 400, 0.75)]),
    ],
)
def test_new_goldens_on_independent_sqlite_fixture(case_id, expected):
    from pathlib import Path

    from scripts.golden import load_cases

    case = next(
        case for case in load_cases(Path(__file__).resolve().parents[2] / "eval/golden") if case.id == case_id
    )
    with sqlite3.connect(":memory:") as db:
        db.execute(
            f"CREATE TABLE {TABLE} (etl_dt TEXT, org_no TEXT, org_nm TEXT, prd_no TEXT, prd_nm TEXT, current_balnc_cny INTEGER)"
        )
        db.execute("CREATE TABLE dim_org (org_no TEXT, parent_org_no TEXT)")
        db.executemany(
            f"INSERT INTO {TABLE} VALUES ('2026-04-30', ?, ?, ?, ?, ?)",
            [
                ("B001", "渝北支行", "LP01", "个人住房贷款", 100),
                ("B001", "渝北支行", "LP03", "小微企业贷款", 300),
                ("B001", "渝北支行", "LP07", "消费贷款", 600),
                ("B002", "江北支行", "LP03", "小微企业贷款", 900),
                ("B0019", "渝北支行营业部", "LP01", "个人住房贷款", 200),
                ("O001", "渝北龙溪分理处", "LP01", "个人住房贷款", 50),
                ("O002", "渝北回兴储蓄所", "LP03", "小微企业贷款", 50),
            ],
        )
        db.executemany(
            "INSERT INTO dim_org VALUES (?, ?)",
            [("B001", "H000"), ("B002", "H000"), ("O001", "B001"), ("O002", "B001")],
        )
        sql = compile_sql(case.intent, semantic_catalog(), scope=case.scope)
        assert db.execute(sql).fetchall() == expected
        assert db.execute(case.sql_path.read_text(encoding="utf-8")).fetchall() == expected


@pytest.mark.parametrize("case_id", ["G-023", "G-024", "G-025", "G-026"])
@pytest.mark.parametrize("dialect", ["postgres", "oracle"])
def test_new_org_goldens_render_parse_and_validate_other_dialects(case_id, dialect):
    from pathlib import Path

    from sqlglot import parse_one

    from app.engine.semantic.bank_mini import validation_catalog
    from app.engine.validation import validate_sql
    from scripts.golden import load_cases

    case = next(
        case for case in load_cases(Path(__file__).resolve().parents[2] / "eval/golden") if case.id == case_id
    )
    sql = compile_sql(case.intent, semantic_catalog(), scope=case.scope, dialect=dialect)
    assert parse_one(sql, read=dialect) is not None
    result = validate_sql(
        sql, catalog=validation_catalog(), intent=case.intent, scope=case.scope, dialect=dialect
    )
    assert result.ok, result.feedback()
