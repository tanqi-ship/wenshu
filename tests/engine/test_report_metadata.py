"""报表声明与明细口径隔离，错误知识在 L0 加载时就拒绝。"""

from dataclasses import replace

import pytest

from app.engine.semantic.bank_mini import REPORT_TABLES, TABLE, semantic_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import LevelDef
from app.engine.semantic.report import ReportDef


def profile(**changes) -> ReportDef:
    return replace(semantic_catalog().reports["npl_ratio"], **changes)


def build(*reports) -> SemanticCatalog:
    base = semantic_catalog()
    return SemanticCatalog.build(
        metrics=base.metrics.values(), dimensions=base.dimensions.values(), reports=reports
    )


def test_report_registration_preserves_plain_formula_and_source() -> None:
    catalog = semantic_catalog()
    assert catalog.metrics["npl_ratio"].source_tables == (TABLE,)
    assert "NULLIF" in catalog.metrics["npl_ratio"].formula
    assert catalog.reports["npl_ratio"].value_column == "npl_rate"
    assert tuple(catalog.reports["npl_ratio"].partitions.values()) == REPORT_TABLES
    assert SemanticCatalog.build().reports == {}


def test_profile_copies_mapping_inputs_and_disallows_mutation() -> None:
    partitions = {"202604": "report_april"}
    dimensions = {"product": LevelDef("prd_no")}
    report = ReportDef("npl_ratio", "npl_rate", dimensions=dimensions, partitions=partitions)
    partitions["202604"] = "wrong_report"
    dimensions.clear()
    assert report.partitions["202604"] == "report_april"
    assert report.dimensions["product"].group_by == "prd_no"
    with pytest.raises(TypeError):
        report.partitions["202604"] = "changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"metric_code": "unknown"},
        {"metric_code": "npl_ratio; DROP TABLE t"},
        {"value_column": ""},
        {"value_column": "SUM(npl_rate)"},
        {"value_column": "t.npl_rate"},
        {"month_column": "stat_month AS x"},
        {"dimensions": []},
        {"partitions": []},
        {"dimensions": {"unknown": LevelDef("x")}},
        {"dimensions": {"product": "prd_no"}},
        {"dimensions": {"product": LevelDef("prd_no", table=TABLE)}},
        {"dimensions": {"product": LevelDef("prd_no", display="MAX(prd_nm)")}},
        {"dimensions": {"product": LevelDef("prd_no", display=None)}},
        {"dimensions": {"product": LevelDef("t.prd_no")}},
        {"dimensions": {"product": LevelDef("STAT_MONTH")}},
        {"dimensions": {"product": LevelDef("npl_ratio")}},
        {"dimensions": {"product": LevelDef("prd_no"), "org": LevelDef("PRD_NO")}},
        {"dimensions": {"product": LevelDef("prd_no", "npl_ratio")}},
        {"partitions": {"202613": "rpt"}},
        {"partitions": {"000001": "rpt"}},
        {"partitions": {"20264": "rpt"}},
        {"partitions": {202604: "rpt"}},
        {"partitions": {"202604": ""}},
        {"partitions": {"202604": "rpt; SELECT 1"}},
        {"partitions": {"202604": "(SELECT 1)"}},
        {"partitions": {"202604": "a.b.c.d"}},
    ],
)
def test_invalid_report_configuration_fails_at_load(changes) -> None:
    with pytest.raises(ValueError):
        build(profile(**changes))


def test_duplicate_or_wrong_type_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="重复"):
        build(profile(), profile())
    with pytest.raises(ValueError, match="ReportDef"):
        build("not a report")


def test_explicit_schema_tables_and_identical_id_display_are_valid() -> None:
    report = profile(
        dimensions={"product": LevelDef("prd_no", "PRD_NO")},
        partitions={"202604": "warehouse.public.report_april"},
    )
    assert build(report).reports["npl_ratio"] is report
