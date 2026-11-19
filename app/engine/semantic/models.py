"""语义层的数据结构。

设计要点：**模型只负责产出 ``Intent``，不产出 SQL 形状。**

``Intent`` 是业务语言（指标 / 时间 / 机构 / 维度 / 过滤 / 对比），
不含任何 SQL 语法。SQL 由编译器从 ``Intent`` + 语义层 + 权限确定性地展开。

这是与 askdb 路线最本质的差别：
askdb 让模型直接产出接近 SQL 的业务结构，再用 5,500 字 prompt 约束它别写错；
这里把"写 SQL"整件事从模型手里拿走，能拿走的错误就再也不会发生。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

TimeGrain = Literal["month_end", "day", "month", "quarter", "year", "range"]
SourceMode = Literal["plain", "report", "union"]
Comparison = Literal["delta", "yoy", "mom", "rate"]


@dataclass(frozen=True, slots=True)
class TimeRange:
    """时间条件。

    *``grain``* 是最容易出错的字段，所以显式建模：

    * ``month_end`` —— 月末时点快照（本项目最常见，"2026年4月末"）
    * ``month`` —— 逐月序列（"每月"、"逐月"），必须保留月份维度
    * ``range`` —— 闭区间（"2026年1月到6月"）
    """

    grain: TimeGrain
    raw: str = ""
    #: 时点快照的具体日期，``grain=month_end`` 时使用。
    points: tuple[date, ...] = ()
    start: date | None = None
    end: date | None = None

    @property
    def is_multi_point(self) -> bool:
        """是否是多时点（同比/环比/双月对比）。

        多时点必须用 ``WHERE snapshot IN (...)`` + ``CASE WHEN`` 一次筛出，
        **禁止**用标量子查询去查另一个时点 —— 这条由 R-COMP-001 强制。
        """
        return len(self.points) > 1


@dataclass(frozen=True, slots=True)
class Filter:
    """一个维度过滤条件。

    *``value``* 必须是**数据库里真实存储的值**（编码或标准全称），
    不是用户口语。编码映射在检索/解析阶段完成，不在编译阶段猜。
    """

    dimension: str
    value: object = None
    operator: Literal["eq", "in", "between", "gte", "lte"] = "eq"
    raw: str = ""


@dataclass(frozen=True, slots=True)
class Intent:
    """用户想查什么。这是模型的唯一输出物。"""

    metric: str | None = None
    time_range: TimeRange | None = None
    org: str | None = None
    dimensions: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    comparison: Comparison | None = None
    #: Top N 的 N。``None`` 表示不是 Top N 查询。
    top_n: int | None = None
    #: Top N 的分组粒度。``"month"`` 表示"每月 Top N"，必须走窗口函数。
    top_n_grain: str | None = None
    source_mode: SourceMode = "plain"
    #: 模型自评的置信度，仅用于日志与降级决策，**不参与"信息是否足够"的判断**。
    #: 那个判断由 :mod:`app.engine.clarify.slots` 的确定性槽位检查负责。
    confidence: float = 0.0
    raw: Mapping[str, object] = field(default_factory=dict)

    @property
    def needs_monthly_topn(self) -> bool:
        return self.top_n is not None and (self.top_n_grain or "") == "month"


# ---------------------------------------------------------------------------
# 从配置（YAML / JSON）构造 Intent —— 黄金集与评测集用
# ---------------------------------------------------------------------------

_TIME_GRAINS = frozenset({"month_end", "day", "month", "quarter", "year", "range"})
_FILTER_OPERATORS = frozenset({"eq", "in", "between", "gte", "lte"})
_COMPARISONS = frozenset({"delta", "yoy", "mom", "rate"})
_SOURCE_MODES = frozenset({"plain", "report", "union"})

_INTENT_KEYS = frozenset(
    {
        "metric",
        "time",
        "org",
        "dimensions",
        "filters",
        "comparison",
        "top_n",
        "top_n_grain",
        "source_mode",
    }
)
_TIME_KEYS = frozenset({"grain", "raw", "points", "start", "end"})
_FILTER_KEYS = frozenset({"dimension", "value", "operator", "raw"})


def _as_mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} 应该是一个映射，当前是 {type(value).__name__}。")
    return value


def _reject_unknown_keys(payload: Mapping[str, object], allowed: frozenset[str], what: str) -> None:
    """未知键直接报错，绝不静默忽略。

    理由：配置里把 ``dimension`` 手滑写成 ``dimensions``，若被忽略，
    黄金集会"通过"——但它测的东西已经悄悄变了。配置文件的错必须响。
    """
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(
            f"{what} 里有不认识的键：{', '.join(unknown)}。允许的键：{', '.join(sorted(allowed))}。"
        )


def _as_date(value: object, what: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{what} 应该是 ISO 日期字符串（如 2026-04-30），当前是 {value!r}。")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{what} 不是合法的 ISO 日期：{value!r}") from exc


def _choice(value: object, allowed: frozenset[str], what: str) -> str:
    text = str(value or "").strip()
    if text not in allowed:
        raise ValueError(f"{what} 只能是 {', '.join(sorted(allowed))}，当前是 {value!r}。")
    return text


def _time_range_from_mapping(raw: object) -> TimeRange:
    payload = _as_mapping(raw, "time")
    _reject_unknown_keys(payload, _TIME_KEYS, "time")

    points_raw = payload.get("points") or ()
    if isinstance(points_raw, str):
        raise ValueError("time.points 应该是日期列表，不是单个字符串。")
    points = tuple(_as_date(point, "time.points") for point in points_raw)

    start = payload.get("start")
    end = payload.get("end")
    return TimeRange(
        grain=_choice(payload.get("grain"), _TIME_GRAINS, "time.grain"),  # type: ignore[arg-type]
        raw=str(payload.get("raw") or ""),
        points=points,
        start=_as_date(start, "time.start") if start is not None else None,
        end=_as_date(end, "time.end") if end is not None else None,
    )


def _filter_from_mapping(raw: object) -> Filter:
    payload = _as_mapping(raw, "filters[]")
    _reject_unknown_keys(payload, _FILTER_KEYS, "filters[]")

    dimension = str(payload.get("dimension") or "").strip()
    if not dimension:
        raise ValueError("filters[] 缺少 dimension。")
    return Filter(
        dimension=dimension,
        value=payload.get("value"),
        operator=_choice(payload.get("operator") or "eq", _FILTER_OPERATORS, "filters[].operator"),  # type: ignore[arg-type]
        raw=str(payload.get("raw") or ""),
    )


def intent_from_mapping(data: Mapping[str, object]) -> Intent:
    """从配置结构构造 :class:`Intent`。黄金集用例与评测集用这个入口。

    支持的结构::

        metric: 不良贷款余额
        time:
          grain: month_end
          points: ["2026-04-30"]
        dimensions: [机构]
        filters:
          - dimension: 机构
            value: "B001"

    三条刻意的约束：

    1. **未知键直接报错**（见 :func:`_reject_unknown_keys`）。
    2. **时间必须给具体日期**，这里**不**推算"4月末 → 4月30日"。
       那个推算在 ``compiler/time.py::month_end_range``，属于编译器侧；
       让加载器去猜日期，等于把不确定性重新请回配置层。
    3. ``metric`` 是**业务名**（可以写同义词），不是 ``MetricDef.code`` ——
       符号表查找是编译器的第①步，配置不该替它做决定。
    """
    _reject_unknown_keys(data, _INTENT_KEYS, "Intent")

    dimensions_raw = data.get("dimensions") or ()
    if isinstance(dimensions_raw, str):
        raise ValueError("dimensions 应该是名称列表，不是单个字符串。")
    filters_raw = data.get("filters") or ()
    if isinstance(filters_raw, (str, Mapping)):
        raise ValueError("filters 应该是列表，每项是一个 dimension/value 映射。")

    time_raw = data.get("time")
    top_n = data.get("top_n")
    comparison = data.get("comparison")
    source_mode = data.get("source_mode")

    return Intent(
        metric=str(data.get("metric") or "").strip() or None,
        time_range=_time_range_from_mapping(time_raw) if time_raw is not None else None,
        org=str(data.get("org") or "").strip() or None,
        dimensions=tuple(str(item).strip() for item in dimensions_raw if str(item).strip()),
        filters=tuple(_filter_from_mapping(item) for item in filters_raw),
        comparison=_choice(comparison, _COMPARISONS, "comparison") if comparison else None,  # type: ignore[arg-type]
        top_n=int(top_n) if top_n is not None else None,
        top_n_grain=str(data.get("top_n_grain") or "").strip() or None,
        source_mode=_choice(source_mode or "plain", _SOURCE_MODES, "source_mode"),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 语义层元数据（从应用库加载，与物理表结构解耦）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnDef:
    name: str
    label: str = ""
    data_type: str = ""
    role: Literal["dimension", "measure", "unknown"] = "unknown"
    unit: str = ""
    synonyms: tuple[str, ...] = ()
    semantic_dimension_code: str = ""
    analyzable: bool = True


@dataclass(frozen=True, slots=True)
class TableDef:
    name: str
    schema: str = ""
    label: str = ""
    columns: tuple[ColumnDef, ...] = ()
    enabled: bool = True

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def column(self, name: str) -> ColumnDef | None:
        lowered = name.lower()
        for column in self.columns:
            if column.name.lower() == lowered:
                return column
        return None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True, slots=True)
class MetricDef:
    """指标定义。``formula`` 是 SQL 表达式片段，由编译器展开。"""

    code: str
    name: str
    formula: str
    unit: str = ""
    domain: str = ""
    source_tables: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()

    @property
    def is_percentage(self) -> bool:
        return self.unit.strip() in {"%", "百分比", "比率", "率"}


@dataclass(frozen=True, slots=True)
class RelationDef:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: Literal["INNER", "LEFT", "RIGHT"] = "INNER"

    def connects(self, left: str, right: str) -> bool:
        pair = {self.left_table.lower(), self.right_table.lower()}
        return pair == {left.lower(), right.lower()}
