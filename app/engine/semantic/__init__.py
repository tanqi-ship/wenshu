"""语义层领域模型。

``intent_from_mapping`` 是"配置 → Intent"的唯一入口（黄金集/评测集用）。
"""

from app.engine.semantic.models import (
    ColumnDef,
    Comparison,
    Filter,
    Intent,
    MetricDef,
    RelationDef,
    SourceMode,
    TableDef,
    TimeGrain,
    TimeRange,
    intent_from_mapping,
)

__all__ = [
    "ColumnDef",
    "Comparison",
    "Filter",
    "Intent",
    "MetricDef",
    "RelationDef",
    "SourceMode",
    "TableDef",
    "TimeGrain",
    "TimeRange",
    "intent_from_mapping",
]
