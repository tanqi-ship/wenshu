"""不可信模型响应的严格边界；独立手写预期，不刷新标准答案。"""

import json
from datetime import date

import pytest

from app.engine.understanding.errors import ExtractionError
from app.engine.understanding.schema import parse_intent


def parse(payload, question="贷款余额 去年 上半年"):
    return parse_intent(json.dumps(payload, ensure_ascii=False), question=question)


def assert_error(payload, code="SCHEMA_INVALID", question="贷款余额 去年 上半年"):
    with pytest.raises(ExtractionError) as exc:
        parse(payload, question)
    assert exc.value.code == "EXTRACTION_" + code
    assert exc.value.message
    return exc.value


BASE = {"metrics": ["贷款余额"], "unresolved": []}


def test_defaults_and_raw():
    intent = parse(BASE)
    assert intent.metric == "贷款余额"
    assert intent.time_range is None
    assert intent.org is None
    assert intent.source_mode == "plain"
    assert intent.confidence == 0
    assert intent.raw == BASE
    assert parse({"metrics": [], "unresolved": []}).metric is None
    assert parse({**BASE, "metrics": ["  未知指标  ", "未知指标"]}).metric == "未知指标"


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "```json\n{}\n```",
        "说明{}",
        "{}{}",
        "{",
        '{"metrics":[],"metrics":[]}',
        '{"a":{"x":1,"x":2}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        pytest.param("[" * 1500 + "]" * 1500, id="deep-json"),
    ],
)
def test_bad_json(text):
    with pytest.raises(ExtractionError) as exc:
        parse_intent(text, question="问题")
    assert exc.value.code == "EXTRACTION_JSON_INVALID"


@pytest.mark.parametrize("value", [None, False, 1, [], pytest.param("x" * 32769, id="oversize")])
def test_response_type_and_limit(value):
    with pytest.raises(ExtractionError) as exc:
        parse_intent(value, question="问题")
    assert exc.value.code == "EXTRACTION_SCHEMA_INVALID"


@pytest.mark.parametrize("root", [None, [], True, 2, "str", {}, {"metrics": []}, {"unresolved": []}])
def test_root_and_required(root):
    assert_error(root)


@pytest.mark.parametrize("key", ["sql", "table", "formula", "scope", "confidence", "org", "metric", "raw"])
def test_unknown_root(key):
    assert_error({**BASE, key: "攻击"})


@pytest.mark.parametrize("field", ["metrics", "dimensions", "unresolved"])
@pytest.mark.parametrize("value", [None, "贷款余额", 2, True, {}, [None], [2], [False], [[]], [" "], [""]])
def test_name_arrays(field, value):
    assert_error({**BASE, field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("comparison", "other"),
        ("comparison", ""),
        ("comparison", []),
        ("source_mode", None),
        ("source_mode", ""),
        ("source_mode", "auto"),
        ("top_n", True),
        ("top_n", 0),
        ("top_n", -1),
        ("top_n", 1.0),
        ("top_n", "2"),
        ("top_n_grain", "global"),
        ("top_n_grain", "month"),
        ("share", {}),
        ("share", []),
        ("share", {"dimension": "产品", "sql": "x"}),
        ("share", {"dimension": 1}),
        ("filters", None),
        ("filters", {}),
    ],
)
def test_strict_root_fields(field, value):
    assert_error({**BASE, field: value})


@pytest.mark.parametrize("comparison", ["delta", "yoy", "mom", "rate"])
def test_comparison_retained(comparison):
    assert parse({**BASE, "comparison": comparison}).comparison == comparison


@pytest.mark.parametrize("value", [None, "", " ", 2, False, [], {}, ["渝北"]])
def test_eq_values(value):
    assert_error({**BASE, "filters": [{"dimension": "机构", "value": value}]})


@pytest.mark.parametrize("value", [None, [], "渝北", 2, True, [None], [2], [True], [["渝北"]], [" "]])
def test_in_values(value):
    assert_error({**BASE, "filters": [{"dimension": "机构", "operator": "in", "value": value}]})


@pytest.mark.parametrize(
    "change",
    [
        {"operator": "gte"},
        {"operator": ""},
        {"dimension": None},
        {"dimension": " "},
        {"raw": None},
        {"sql": "x"},
    ],
)
def test_filter_fields(change):
    assert_error({**BASE, "filters": [{"dimension": "机构", "value": "渝北", **change}]})


def test_values_not_normalized():
    intent = parse({**BASE, "filters": [{"dimension": "机构", "operator": "in", "value": [" A B ", "ab"]}]})
    assert intent.filters[0].value == [" A B ", "ab"]
    assert intent.filters[0].raw == ""


@pytest.mark.parametrize(
    "time",
    [
        {"grain": "other", "points": ["2026-04-30"]},
        {"grain": "day", "points": "2026-04-30"},
        {"grain": "day", "raw": None},
        {"grain": "day", "sql": "x"},
        [],
    ],
)
def test_time_schema(time):
    assert_error({**BASE, "time": time})


@pytest.mark.parametrize(
    "point",
    [
        "20260430",
        "2026-4-30",
        "2026-04-31",
        "2025-02-29",
        "2026-04-30T00:00:00",
        "04-30",
        20260430,
        True,
        None,
    ],
)
def test_dates(point):
    assert_error({**BASE, "time": {"grain": "day", "points": [point]}}, "TIME_INVALID")


@pytest.mark.parametrize(
    "time",
    [
        {"grain": "day"},
        {"grain": "month_end", "points": ["2026-04-29"]},
        {"grain": "day", "start": "2026-04-01", "end": "2026-04-30"},
        {"grain": "range", "points": ["2026-04-30"]},
        {"grain": "range", "start": "2026-04-01"},
        {"grain": "range", "start": None, "end": "2026-04-30"},
        {"grain": "range", "start": "2026-05-01", "end": "2026-04-30"},
        {"grain": "month", "points": ["2026-04-30"], "start": "2026-04-01", "end": "2026-04-30"},
    ],
)
def test_time_shapes(time):
    assert_error({**BASE, "time": time}, "TIME_INVALID")


@pytest.mark.parametrize("grain", ["day", "month_end", "month", "quarter", "year"])
def test_time_points(grain):
    intent = parse({**BASE, "time": {"grain": grain, "points": ["2028-02-29"]}})
    assert intent.time_range.points == (date(2028, 2, 29),)


@pytest.mark.parametrize("grain", ["range", "month", "quarter", "year"])
def test_ranges(grain):
    intent = parse({**BASE, "time": {"grain": grain, "start": "2027-01-01", "end": "2027-12-31"}})
    assert intent.time_range.start == date(2027, 1, 1)


def test_error_priority_and_fragments():
    data = {"metrics": ["贷款余额", "不良余额"], "unresolved": ["去年"]}
    assert_error({**data, "top_n": "2"})
    assert_error({**data, "time": {"grain": "day"}}, "TIME_INVALID")
    assert_error(data, "MULTI_METRIC_UNSUPPORTED")
    assert_error({**BASE, "metrics": ["贷款余额", "余额"]}, "MULTI_METRIC_UNSUPPORTED")
    error = assert_error({**BASE, "unresolved": ["去年"]}, "UNRESOLVED")
    assert error.fragments == ("去年",)
    assert error.path == "unresolved"
    assert_error({**BASE, "unresolved": ["不存在的片段"]})


@pytest.mark.parametrize(
    "text",
    [
        '{"metrics":[],"unresolved":[],"time":{"grain":"day","grain":"month_end"}}',
        '{"metrics":[],"unresolved":[],"filters":[{"dimension":"机构","value":"渝北","value":"江北"}]}',
    ],
)
def test_nested_duplicate_keys(text):
    with pytest.raises(ExtractionError) as exc:
        parse_intent(text, question="问题")
    assert exc.value.code == "EXTRACTION_JSON_INVALID"


def test_length_boundary_and_json_string_brackets():
    text = json.dumps(BASE, ensure_ascii=False)
    intent = parse_intent(text + " " * (32768 - len(text)), question="贷款余额")
    assert intent.metric == "贷款余额"
    intent = parse({"metrics": ['[{}]"\\' * 100], "unresolved": []})
    assert len(intent.metric) > 64


@pytest.mark.parametrize(
    "payload",
    [
        {"time": {"grain": "day", "points": []}},
        {"time": {"grain": "day", "points": None}},
        {"time": {"grain": "range", "start": "2026-01-01", "end": "2026-12-31", "raw": 1}},
        {"time": {"grain": "day", "points": ["2026-01-01"], "scope": {}}},
        {"share": {"dimension": ""}},
        {"filters": [None]},
        {"filters": ["机构"]},
    ],
)
def test_more_nested_fields(payload):
    expected = "TIME_INVALID" if payload == {"time": {"grain": "day", "points": []}} else "SCHEMA_INVALID"
    assert_error({**BASE, **payload}, expected)


@pytest.mark.parametrize("mode", ["plain", "report", "union"])
def test_source_modes_and_independent_share(mode):
    intent = parse({**BASE, "source_mode": mode, "share": {"dimension": "未知维度"}})
    assert intent.source_mode == mode
    assert intent.share.dimension == "未知维度"
    assert intent.comparison is None


def test_global_topn_not_monthly():
    intent = parse({**BASE, "top_n": 2})
    assert intent.top_n == 2
    assert intent.top_n_grain is None
