"""严格 JSON/schema 边界；不修复、不补日期、不选择最相近实体。"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import replace
from datetime import date

from app.engine.semantic.models import Intent, intent_from_mapping
from app.engine.understanding.errors import ExtractionError

MAX_RESPONSE_CHARS = 32768
ROOT_KEYS = frozenset(
    {
        "metrics",
        "time",
        "dimensions",
        "filters",
        "comparison",
        "top_n",
        "top_n_grain",
        "source_mode",
        "share",
        "unresolved",
    }
)


def fail(kind: str, message: str, path: str = "") -> None:
    raise ExtractionError("EXTRACTION_" + kind, message, path=path)


def object_fields(value: object, allowed: set | frozenset, path: str) -> dict:
    if not isinstance(value, dict):
        fail("SCHEMA_INVALID", "必须是对象。", path)
    if set(value) - allowed:
        fail("SCHEMA_INVALID", "包含未知字段：" + ", ".join(sorted(set(value) - allowed)), path)
    return value


def string(value: object, path: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        fail("SCHEMA_INVALID", "必须是非空字符串。" if nonempty else "必须是字符串。", path)
    return value


def strings(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        fail("SCHEMA_INVALID", "必须是字符串数组。", path)
    return [string(item, f"{path}[{index}]") for index, item in enumerate(value)]


def choice(value: object, allowed: tuple, path: str) -> None:
    if value not in allowed:
        fail("SCHEMA_INVALID", "非法枚举值。", path)


def strict_date(value: object, path: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        fail("TIME_INVALID", "日期必须是 YYYY-MM-DD。", path)
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail("TIME_INVALID", "无效日历日期。", path)


def validate_time(value: object) -> None:
    data = object_fields(value, {"grain", "points", "start", "end", "raw"}, "time")
    grain = data.get("grain")
    choice(grain, ("month_end", "day", "month", "quarter", "year", "range"), "time.grain")
    string(data.get("raw", ""), "time.raw", nonempty=False)
    raw_points = data.get("points", [])
    if not isinstance(raw_points, list):
        fail("SCHEMA_INVALID", "必须是日期数组。", "time.points")
    points = [strict_date(p, f"time.points[{i}]") for i, p in enumerate(raw_points)]
    start, end = data.get("start"), data.get("end")
    if (start is None) != (end is None):
        fail("TIME_INVALID", "区间必须同时给出起止日期。", "time")
    interval = start is not None
    if interval:
        start_date = strict_date(start, "time.start")
        end_date = strict_date(end, "time.end")
        if start_date > end_date:
            fail("TIME_INVALID", "起始日期晚于终止日期。", "time")
    if (bool(points) and interval) or (not points and not interval):
        fail("TIME_INVALID", "必须且只能给时点或完整区间。", "time")
    if grain in ("month_end", "day") and not points:
        fail("TIME_INVALID", "该粒度必须使用时点。", "time.points")
    if grain == "range" and not interval:
        fail("TIME_INVALID", "range 必须使用完整区间。", "time")
    if grain == "month_end" and any(p.day != calendar.monthrange(p.year, p.month)[1] for p in points):
        fail("TIME_INVALID", "month_end 必须为真实月末。", "time.points")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 键：" + key)
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("不允许非有限常量：" + value)


def _check_depth(text: str) -> None:
    # 在 json.loads 前限制嵌套，避免依赖宿主/pytest 的递归阈值。字符串内括号不计数。
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("JSON 嵌套深度超过 64。")
        elif char in "]}":
            depth -= 1


def parse_intent(text: str, *, question: str) -> Intent:
    """完整验证所有字段之后，才检查多指标、未解决片段并构造 Intent。"""
    if not isinstance(text, str) or len(text) > MAX_RESPONSE_CHARS:
        fail("SCHEMA_INVALID", "响应必须是长度不超过 32768 字符的字符串。", "response")
    try:
        _check_depth(text)
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise ExtractionError(
            "EXTRACTION_JSON_INVALID", "响应不是严格单一 JSON：" + str(exc), path="response"
        ) from exc
    data = object_fields(data, ROOT_KEYS, "response")
    if not {"metrics", "unresolved"} <= data.keys():
        fail("SCHEMA_INVALID", "metrics 和 unresolved 必填。", "response")
    metrics = list(dict.fromkeys(item.strip() for item in strings(data["metrics"], "metrics")))
    strings(data.get("dimensions", []), "dimensions")
    unresolved = strings(data["unresolved"], "unresolved")
    for index, fragment in enumerate(unresolved):
        if fragment not in question:
            fail("SCHEMA_INVALID", "未解决片段必须出现在原问题。", f"unresolved[{index}]")
    choice(data.get("comparison"), (None, "delta", "yoy", "mom", "rate"), "comparison")
    choice(data.get("source_mode", "plain"), ("plain", "report", "union"), "source_mode")
    top_n = data.get("top_n")
    if top_n is not None and (type(top_n) is not int or top_n <= 0):
        fail("SCHEMA_INVALID", "top_n 必须是正整数或 null。", "top_n")
    choice(data.get("top_n_grain"), (None, "month"), "top_n_grain")
    if data.get("top_n_grain") is not None and top_n is None:
        fail("SCHEMA_INVALID", "有排名粒度必须有 top_n。", "top_n_grain")
    share = data.get("share")
    if share is not None:
        object_fields(share, {"dimension"}, "share")
        string(share.get("dimension"), "share.dimension")
    filters = data.get("filters", [])
    if not isinstance(filters, list):
        fail("SCHEMA_INVALID", "filters 必须是数组。", "filters")
    for index, item in enumerate(filters):
        path = f"filters[{index}]"
        object_fields(item, {"dimension", "operator", "value", "raw"}, path)
        string(item.get("dimension"), path + ".dimension")
        string(item.get("raw", ""), path + ".raw", nonempty=False)
        operator = item.get("operator", "eq")
        choice(operator, ("eq", "in"), path + ".operator")
        if operator == "eq":
            string(item.get("value"), path + ".value")
        else:
            values = strings(item.get("value"), path + ".value")
            if not values:
                fail("SCHEMA_INVALID", "IN 数组不得为空。", path + ".value")
    if data.get("time") is not None:
        validate_time(data["time"])
    if len(metrics) > 1:
        fail("MULTI_METRIC_UNSUPPORTED", "首版不支持多个不同指标短语。", "metrics")
    if unresolved:
        raise ExtractionError(
            "EXTRACTION_UNRESOLVED",
            "存在尚未解决的原问题片段。",
            path="unresolved",
            fragments=tuple(unresolved),
        )
    mapping = {k: v for k, v in data.items() if k not in {"metrics", "unresolved"}}
    mapping["metric"] = metrics[0] if metrics else None
    return replace(intent_from_mapping(mapping), raw=data)
