"""单槽补答协议：模型信号受严格结构约束，不修改其他业务字段。"""

import json

import pytest

from app.engine.clarify.session.errors import SessionError
from app.engine.clarify.session.reply import parse_reply


def parse(data, slot="time_range", answer="2026年4月末 去年 改查江北"):
    return parse_reply(json.dumps(data, ensure_ascii=False), slot=slot, answer=answer)


BASE = {
    "slot": "time_range",
    "status": "filled",
    "value": {"grain": "month_end", "points": ["2026-04-30"]},
    "unresolved": [],
}


def test_valid_time():
    result = parse(BASE)
    assert result.status == "filled"
    assert result.value == BASE["value"]


def test_metric_only_exact_duplicates_removed():
    result = parse({**BASE, "slot": "metric", "value": ["  未知指标 ", "未知指标"]}, slot="metric")
    assert result.value == ["未知指标"]
    result = parse({**BASE, "slot": "metric", "value": ["贷款余额", "不良贷款余额"]}, slot="metric")
    assert result.status == "restart_required"
    assert result.value is None


@pytest.mark.parametrize(
    "status,fragments", [("unclear", ["去年"]), ("restart_required", []), ("restart_required", ["改查江北"])]
)
def test_semantic_signals(status, fragments):
    result = parse({**BASE, "status": status, "value": None, "unresolved": fragments})
    assert result.status == status
    assert result.fragments == tuple(fragments)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "```json {} ```",
        "{}{}",
        '{"slot":"metric","slot":"time_range"}',
        '{"x":NaN}',
        '{"x":Infinity}',
        pytest.param("[" * 100 + "]" * 100, id="depth"),
    ],
)
def test_invalid_json(text):
    with pytest.raises(SessionError) as exc:
        parse_reply(text, slot="metric", answer="查询")
    assert exc.value.code == "SESSION_REPLY_JSON_INVALID"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {**BASE, "sql": "x"},
        {**BASE, "slot": "metric"},
        {**BASE, "status": "ready"},
        {**BASE, "unresolved": None},
        {**BASE, "unresolved": ["去年"]},
        {**BASE, "status": "unclear", "value": None},
        {**BASE, "status": "unclear", "value": None, "unresolved": ["不存在"]},
        {**BASE, "status": "restart_required"},
        {**BASE, "value": {"grain": "month_end", "points": ["2026-04-30"], "org": "江北"}},
    ],
)
def test_schema_rejections(data):
    with pytest.raises(SessionError) as exc:
        parse(data)
    assert exc.value.code == "SESSION_REPLY_SCHEMA_INVALID"


@pytest.mark.parametrize("value", [None, "贷款余额", [], [None], [True], [1], [" "], [["贷款余额"]]])
def test_metric_rejections(value):
    with pytest.raises(SessionError) as exc:
        parse({**BASE, "slot": "metric", "value": value}, slot="metric")
    assert exc.value.code == "SESSION_REPLY_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "value",
    [
        {"grain": "day"},
        {"grain": "month_end", "points": ["2026-04-29"]},
        {"grain": "day", "points": ["20260430"]},
        {"grain": "range", "start": "2026-05-01", "end": "2026-04-30"},
    ],
)
def test_invalid_time(value):
    with pytest.raises(SessionError) as exc:
        parse({**BASE, "value": value})
    assert exc.value.code == "SESSION_REPLY_TIME_INVALID"


@pytest.mark.parametrize("value", [None, 1, pytest.param("x" * 32769, id="oversize")])
def test_response_limit(value):
    with pytest.raises(SessionError) as exc:
        parse_reply(value, slot="metric", answer="查询")
    assert exc.value.code == "SESSION_REPLY_SCHEMA_INVALID"


@pytest.mark.parametrize("value", [["\ud800"], ["贷款余额", "各项贷款余额"]])
def test_encoding_and_synonyms_not_silently_merged(value):
    if value == ["\ud800"]:
        with pytest.raises(SessionError, match="SESSION_REPLY_SCHEMA_INVALID"):
            parse({**BASE, "slot": "metric", "value": value}, slot="metric")
    else:
        assert parse({**BASE, "slot": "metric", "value": value}, slot="metric").status == "restart_required"


@pytest.mark.parametrize(
    "grain,value",
    [
        ("day", {"grain": "day", "points": ["2028-02-29"]}),
        ("range", {"grain": "range", "start": "2026-01-01", "end": "2026-12-31"}),
        ("month", {"grain": "month", "points": ["2026-04-30"]}),
    ],
)
def test_valid_absolute_shapes(grain, value):
    assert parse({**BASE, "value": value}).value["grain"] == grain


def test_nested_duplicate_keys_and_string_brackets():
    text = '{"slot":"time_range","status":"filled","value":{"grain":"day","grain":"month_end","points":["2026-04-30"]},"unresolved":[]}'
    with pytest.raises(SessionError, match="SESSION_REPLY_JSON_INVALID"):
        parse_reply(text, slot="time_range", answer="2026年4月末")
    result = parse({**BASE, "slot": "metric", "value": ['[{}]"\\' * 100]}, slot="metric")
    assert result.status == "filled"
