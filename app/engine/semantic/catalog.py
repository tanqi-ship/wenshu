"""语义层容器：编译器唯一的"知识来源"。

编译器不读数据库、不读 prompt、不看模型输出——它只读这个对象。

设计要点：

* **查找是确定性的**：按 code / name / synonyms 匹配，全部 casefold + 去空白。
  找不到就是找不到，返回 ``None``，由编译器转成明确错误码。
* **不持有校验用的 Catalog**：那份只关心名字存不存在，属于
  :mod:`app.engine.validation`，两者刻意分开（见 docs/ARCHITECTURE.md）。
* **不可变**：``frozen=True``。语义层在编译过程中被改动是最难查的一类 bug。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.engine.semantic.dimension import DimensionDef
from app.engine.semantic.models import MetricDef, RelationDef


def _norm(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


@dataclass(frozen=True, slots=True)
class SemanticCatalog:
    """指标 / 维度 / 表关系 / 码值。"""

    metrics: Mapping[str, MetricDef] = field(default_factory=dict)
    dimensions: Mapping[str, DimensionDef] = field(default_factory=dict)
    relations: tuple[RelationDef, ...] = ()
    #: 维度编码 -> {中文值: 数据库存储值}
    code_map: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    # -- 指标 --------------------------------------------------------------
    def metric(self, term: str) -> MetricDef | None:
        """按 code / 中文名 / 同义词查指标。"""
        wanted = _norm(term)
        if not wanted:
            return None
        for key, metric in self.metrics.items():
            if _norm(key) == wanted:
                return metric
        for metric in self.metrics.values():
            if _norm(metric.name) == wanted:
                return metric
            if any(_norm(alias) == wanted for alias in metric.synonyms):
                return metric
        return None

    def metric_tables(self, metric: MetricDef) -> tuple[str, ...]:
        """指标依赖的物理表。空 = 配置缺失，编译器据此报 UNKNOWN_SOURCE。"""
        return tuple(name for name in metric.source_tables if name)

    # -- 维度 --------------------------------------------------------------
    def dimension(self, term: str) -> DimensionDef | None:
        wanted = _norm(term)
        if not wanted:
            return None
        for key, dim in self.dimensions.items():
            if _norm(key) == wanted:
                return dim
        for dim in self.dimensions.values():
            if _norm(dim.name) == wanted:
                return dim
            if any(_norm(alias) == wanted for alias in dim.synonyms):
                return dim
        return None

    # -- 码值 --------------------------------------------------------------
    def code(self, dimension_code: str, value: object) -> str | None:
        """把业务显示值翻成数据库存储值。

        已经是存储值（大小写完全一致）时原样返回 —— 让调用方可以幂等调用。
        """
        mapping = self.code_map.get(dimension_code) or {}
        if not mapping:
            return None
        raw = str(value if value is not None else "").strip()
        if not raw:
            return None
        if raw in mapping:
            return mapping[raw]
        wanted = _norm(raw)
        for display, stored in mapping.items():
            if _norm(display) == wanted or _norm(stored) == wanted:
                return stored
        return None

    # -- 表关系 ------------------------------------------------------------
    def relation_between(self, left: str, right: str) -> RelationDef | None:
        for relation in self.relations:
            if relation.connects(left, right):
                return relation
        return None

    # -- 构造 --------------------------------------------------------------
    @classmethod
    def build(
        cls,
        *,
        metrics: Iterable[MetricDef] = (),
        dimensions: Iterable[DimensionDef] = (),
        relations: Iterable[RelationDef] = (),
        code_map: Mapping[str, Mapping[str, str]] | None = None,
    ) -> SemanticCatalog:
        """从序列构造，自动按 code 建索引。测试与 fixture 用。"""
        return cls(
            metrics={metric.code: metric for metric in metrics},
            dimensions={dim.code: dim for dim in dimensions},
            relations=tuple(relations),
            code_map=dict(code_map or {}),
        )

    def __bool__(self) -> bool:
        return bool(self.metrics or self.dimensions)
