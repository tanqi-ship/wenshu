"""预计算报表来源：业务口径、固定粒度和月份白名单，不保存任意 SQL。"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from app.engine.semantic.dimension import LevelDef

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}")


@dataclass(frozen=True, slots=True)
class ReportDef:
    metric_code: str
    value_column: str
    month_column: str = "stat_month"
    dimensions: Mapping[str, LevelDef] = field(default_factory=dict)
    partitions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 复制后只读；外部修改原字典不能改变一次编译的口径。
        if isinstance(self.dimensions, Mapping):
            object.__setattr__(self, "dimensions", MappingProxyType(dict(self.dimensions)))
        if isinstance(self.partitions, Mapping):
            object.__setattr__(self, "partitions", MappingProxyType(dict(self.partitions)))

    def validate(self, *, metric_codes: Collection[str], dimension_codes: Collection[str]) -> None:
        """L0 构造时 ValueError；手工目录绕过加载校验时由编译器转为稳定错误。"""
        if not _identifier(self.metric_code) or self.metric_code not in metric_codes:
            raise ValueError("报表 metric_code 必须对应已登记的指标。")
        if not _identifier(self.value_column) or not _identifier(self.month_column):
            raise ValueError("报表值列和月份列必须是单一列名，不能包含表达式。")
        if not isinstance(self.dimensions, Mapping) or not isinstance(self.partitions, Mapping):
            raise ValueError("报表 dimensions/partitions 必须是映射。")
        outputs = ["stat_month", self.metric_code.casefold()]
        for code, level in self.dimensions.items():
            if not isinstance(code, str) or code not in dimension_codes or not isinstance(level, LevelDef):
                raise ValueError("报表维度必须对应已登记的业务编码和 LevelDef。")
            if level.table != "" or not _identifier(level.group_by):
                raise ValueError("报表维度仅能读取当前分表列，不能配置外部来源。")
            if not isinstance(level.display, str) or (level.display and not _identifier(level.display)):
                raise ValueError("报表展示列必须为空或单一列名。")
            outputs.append(level.group_by.casefold())
            if level.display and level.display.casefold() != level.group_by.casefold():
                outputs.append(level.display.casefold())
        if len(set(outputs)) != len(outputs):
            raise ValueError("报表输出别名冲突，不能覆盖月份、维度或指标。")
        for month, table in self.partitions.items():
            if not isinstance(month, str) or re.fullmatch(r"[0-9]{6}", month) is None:
                raise ValueError("报表月份必须是 YYYYMM 字符串。")
            try:
                date(int(month[:4]), int(month[4:]), 1)
            except ValueError as exc:
                raise ValueError(f"非法报表月份：{month}。") from exc
            if not isinstance(table, str) or _TABLE_NAME.fullmatch(table) is None:
                raise ValueError("报表物理表名必须显式登记，可带 schema，但不能是 SQL 片段。")


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
