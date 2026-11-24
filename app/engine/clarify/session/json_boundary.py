"""会话协议共用严格 JSON 边界；错误代码由调用者指定。"""

import json

from app.engine.clarify.session.errors import SessionError, fail


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 键：" + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("非有限 JSON 常量：" + value)


def strict_loads(text, *, limit, type_code, json_code):
    if not isinstance(text, str) or len(text) > limit:
        fail(type_code, "响应类型或字符上限非法。")
    try:
        # 在loads前约束深度，字符串里的括号不算嵌套。
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
                    raise ValueError("嵌套超过64层。")
            elif char in "]}":
                depth -= 1
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise SessionError("SESSION_" + json_code, "严格JSON解析失败。") from exc


def encode_json(value, *, code="STATE_INVALID"):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        text.encode("utf-8")
        return text
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SessionError("SESSION_" + code, "不是受支持的UTF-8 JSON数据。") from exc
