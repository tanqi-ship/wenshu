"""v1受限快照；显式业务字段是权威，raw只作诊断。"""

from dataclasses import asdict, fields, replace
from uuid import UUID

from app.engine.clarify.preparation import Clarification
from app.engine.clarify.session.errors import SessionError, fail
from app.engine.clarify.session.json_boundary import encode_json, strict_loads
from app.engine.clarify.session.models import SessionState
from app.engine.clarify.session.reply import parse_reply
from app.engine.semantic.models import Intent
from app.engine.understanding.schema import parse_intent

MAX_STATE_CHARS = 262144
STATE_FIELDS = {f.name for f in fields(SessionState)} | {"format_version"}


def intent_payload(intent: Intent) -> dict:
    if not isinstance(intent, Intent) or intent.org is not None or intent.confidence != 0:
        fail("STATE_INVALID", "会话仅保存严格抽取的Intent，不接受org/置信度授权。")
    time = intent.time_range
    return {
        "metrics": [intent.metric] if intent.metric is not None else [],
        "time": None
        if time is None
        else {
            "grain": time.grain,
            "points": [p.isoformat() for p in time.points],
            "start": time.start.isoformat() if time.start else None,
            "end": time.end.isoformat() if time.end else None,
            "raw": time.raw,
        },
        "dimensions": list(intent.dimensions),
        "filters": [asdict(f) for f in intent.filters],
        "comparison": intent.comparison,
        "top_n": intent.top_n,
        "top_n_grain": intent.top_n_grain,
        "source_mode": intent.source_mode,
        "share": asdict(intent.share) if intent.share else None,
        "unresolved": [],
    }


def _text(value, *, maximum=4096, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError("字符串字段类型/长度非法。")
    value.encode("utf-8")
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("必须是严格整数且不低于下限。")
    return value


def _clarification(data):
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {f.name for f in fields(Clarification)}:
        raise ValueError("澄清字段非法。")
    if data["reason"] not in ("missing_slot", "unresolved", "multi_metric"):
        raise ValueError("未知澄清类型。")
    _text(data["question"], maximum=32768)
    if data["slot"] not in (None, "metric", "time_range"):
        raise ValueError("未知槽位。")
    if not isinstance(data["fragments"], list):
        raise ValueError("片段必须数组。")
    for f in data["fragments"]:
        _text(f)
    for name in ("source_error_code", "source_error_path"):
        if data[name] is not None:
            _text(data[name], empty=True)
    reason = data["reason"]
    if reason == "missing_slot" and (
        data["slot"] is None
        or data["fragments"]
        or data["source_error_code"] is not None
        or data["source_error_path"] is not None
    ):
        raise ValueError("缺槽位澄清契约非法。")
    if reason == "unresolved" and (
        data["slot"] is not None
        or not data["fragments"]
        or data["source_error_code"] != "EXTRACTION_UNRESOLVED"
    ):
        raise ValueError("未解决片段契约非法。")
    if reason == "multi_metric" and (
        data["slot"] != "metric"
        or data["fragments"]
        or data["source_error_code"] != "EXTRACTION_MULTI_METRIC_UNSUPPORTED"
    ):
        raise ValueError("多指标契约非法。")
    return Clarification(**{**data, "fragments": tuple(data["fragments"])})


def _decode(data):
    if (
        not isinstance(data, dict)
        or set(data) != STATE_FIELDS
        or type(data["format_version"]) is not int
        or data["format_version"] != 1
    ):
        raise ValueError("快照字段或格式版本非法。")
    _text(data["owner_key"], maximum=128)
    _text(data["session_id"], maximum=36)
    if str(UUID(data["session_id"])) != data["session_id"]:
        raise ValueError("会话ID必须为规范UUID。")
    _text(data["catalog_revision"], maximum=128)
    digest = _text(data["catalog_digest"], maximum=64)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("目录摘要非法。")
    for key in ("original_question", "accepted_question"):
        _text(data[key])
    _text(data["note"], maximum=4096, empty=True)
    _integer(data["version"], 1)
    rounds = _integer(data["rounds_used"])
    budget = _integer(data["max_rounds"])
    if rounds > budget:
        raise ValueError("追问次数超过预算。")
    if data["status"] not in (
        "awaiting_slot",
        "awaiting_question",
        "slots_complete",
        "budget_exhausted",
        "restart_required",
    ):
        raise ValueError("状态非法。")
    if data["pending_slot"] not in (None, "metric", "time_range"):
        raise ValueError("待补槽位非法。")
    accepted = data["accepted_intent"]
    intent = None
    if accepted is not None:
        if (
            not isinstance(accepted, dict)
            or set(accepted) != {"payload", "raw"}
            or not isinstance(accepted["raw"], dict)
        ):
            raise ValueError("Intent存储结构非法。")
        intent = replace(
            parse_intent(encode_json(accepted["payload"]), question=data["accepted_question"]),
            raw=accepted["raw"],
        )
    clarification = _clarification(data["clarification"])
    status = data["status"]
    pending = data["pending_slot"]
    if status == "awaiting_slot":
        if (
            intent is None
            or clarification is None
            or clarification.reason != "missing_slot"
            or pending != clarification.slot
            or rounds < 1
        ):
            raise ValueError("待补状态必须有Intent和追问。")
        missing = "metric" if intent.metric is None else "time_range" if intent.time_range is None else None
        if pending != missing:
            raise ValueError("待补槽位与真实缺失不匹配。")
    elif status == "awaiting_question":
        if (
            intent is not None
            or pending is not None
            or clarification is None
            or clarification.reason not in ("unresolved", "multi_metric")
            or rounds < 1
        ):
            raise ValueError("重述状态不得持有部分Intent。")
        if any(f not in data["accepted_question"] for f in clarification.fragments):
            raise ValueError("片段不在当前问题中。")
    else:
        if pending is not None or clarification is not None:
            raise ValueError("终止状态不得保留活跃追问。")
        if status == "slots_complete" and (
            intent is None or intent.metric is None or intent.time_range is None
        ):
            raise ValueError("完成状态缺必需字段。")
        if status == "budget_exhausted" and (
            rounds != budget
            or (intent is not None and intent.metric is not None and intent.time_range is not None)
        ):
            raise ValueError("预算耗尽状态矛盾。")
    diagnostics = data["reply_diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > budget:
        raise ValueError("补答诊断数组非法。")
    for diag in diagnostics:
        if (
            not isinstance(diag, dict)
            or set(diag) != {"answer", "patch"}
            or not isinstance(diag["patch"], dict)
        ):
            raise ValueError("诊断字段非法。")
        _text(diag["answer"])
        parse_reply(encode_json(diag["patch"]), slot=diag["patch"].get("slot"), answer=diag["answer"])
    return SessionState(
        **{
            k: v
            for k, v in data.items()
            if k not in {"format_version", "accepted_intent", "clarification", "reply_diagnostics"}
        },
        accepted_intent=intent,
        clarification=clarification,
        reply_diagnostics=tuple(diagnostics),
    )


def decode_state(text: str) -> SessionState:
    try:
        data = strict_loads(text, limit=MAX_STATE_CHARS, type_code="STATE_INVALID", json_code="STATE_INVALID")
        encode_json(data)  # 包括诊断内部的非有限数/坏编码也不能恢复。
        return _decode(data)
    except SessionError as exc:
        if exc.code == "SESSION_STATE_INVALID":
            raise
        raise SessionError("SESSION_STATE_INVALID", "快照业务结构非法。") from exc
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        raise SessionError("SESSION_STATE_INVALID", "快照数据或状态关系非法。") from exc


def encode_state(state: SessionState) -> str:
    try:
        data = {f.name: getattr(state, f.name) for f in fields(SessionState)}
        intent = state.accepted_intent
        data["accepted_intent"] = (
            {"payload": intent_payload(intent), "raw": intent.raw} if intent is not None else None
        )
        data["clarification"] = asdict(state.clarification) if state.clarification else None
        text = encode_json({"format_version": 1, **data})
        decode_state(text)  # 写入前使用同一严格恢复边界，两个适配器不能存入坏快照。
        return text
    except SessionError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise SessionError("SESSION_STATE_INVALID", "无法编码会话快照。") from exc
