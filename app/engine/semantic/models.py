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
