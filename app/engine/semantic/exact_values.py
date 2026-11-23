"""严格业务值的精确登记；不依赖编译器、SQL 或数据库。

别名和存储编码在同一 token 空间收集候选，绝不使用第一条命中。
异常 reason 由调用方翻译成自己的加载/编译错误，不产生反向依赖。
"""

from collections.abc import Mapping


class ExactValueError(ValueError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


def check_value_kind(kind: object) -> None:
    if kind not in ("coded", "high_cardinality"):
        raise ExactValueError("invalid", f"不支持的维度 value_kind：{kind!r}。")


def exact_index(mapping: object) -> dict[str, str]:
    """校验已提供的映射并建立无歧义索引；空映射可加载。"""
    if not isinstance(mapping, Mapping):
        raise ExactValueError("invalid", "严格码值登记必须是字符串到字符串的映射。")
    candidates: dict[str, set[str]] = {}
    for alias, stored in mapping.items():
        if (
            not isinstance(alias, str)
            or not alias.strip()
            or not isinstance(stored, str)
            or not stored.strip()
            or stored != stored.strip()
        ):
            raise ExactValueError("invalid", "严格登记的键和值须为非空字符串，编码不能带首尾空白。")
        for token in (alias.strip(), stored):
            candidates.setdefault(token, set()).add(stored)
    for token, values in candidates.items():
        if len(values) != 1:
            raise ExactValueError("ambiguous", f"机构 token {token!r} 对应多个编码：{sorted(values)}。")
    return {token: next(iter(values)) for token, values in candidates.items()}


def resolve_exact(mapping: object, value: object) -> str:
    index = exact_index(mapping)
    if not index:
        raise ExactValueError("missing", "严格维度未登记任何码值，不能透传业务过滤值。")
    if not isinstance(value, str) or value.strip() not in index:
        raise ExactValueError("unknown", f"业务取值 {value!r} 未精确登记。")
    return index[value.strip()]
