"""维度定义。

**这是 askdb 缺失的那块，也是本项目相对它最重要的新增。**

askdb 里"维度"不是一张表，而是三个散落的字符串约定：

* ``db_column_meta.column_role = 'dimension'`` —— 区分维度/度量
* ``db_column_meta.semantic_dimension_code = 'higher_level_org_no'`` —— 自由文本
* ``synonym_dict.dimension_code`` —— 靠 ``casefold()`` 字符串相等与上面对齐

结果：「网点该用哪个物理列」这条业务知识，只能靠 5,500 字 prompt 里的一行
散文去告诉模型，而模型每次要不要遵守是概率问题。

这里把它变成字典查询。见 docs/ARCHITECTURE.md 第二节。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

ValueKind = Literal["coded", "high_cardinality"]


@dataclass(frozen=True, slots=True)
class LevelDef:
    """一个维度在某口径/粒度下对应的物理列。

    * ``group_by`` —— 用于 GROUP BY / WHERE 的稳定编号列（``org_no``）
    * ``display`` —— 需要展示时用的名称列（``org_nm``），**永远只出现在
      ``MAX()`` 里，绝不进 GROUP BY**（规则 R-NAME-001）
    """

    group_by: str
    display: str = ""


@dataclass(frozen=True, slots=True)
class DimensionDef:
    """业务维度。

    ``levels`` 的 key 是**口径或粒度**，例如机构维度：

        levels = {
            "financial":           LevelDef("org_no",        "org_nm"),
            "customer_management": LevelDef("belong_org_no", "belong_org_nm"),
        }

    选哪个 level 由 L1/L2 判定（``Intent.dimensions`` 里的限定词），
    **编译器不猜**——猜就是 askdb 的老路。
    """

    code: str
    name: str
    #: 口径/粒度 -> 物理列。至少要有一个。
    levels: Mapping[str, LevelDef]
    #: 未指定 level 时用哪个 key。
    default_level: str = ""
    synonyms: tuple[str, ...] = ()
    #: ``high_cardinality`` 的取值（机构名）**不能靠模糊匹配**，
    #: 必须精确等值或别名命中（ADR-0002）。
    value_kind: ValueKind = "coded"

    def resolve_level(self, key: str | None = None) -> tuple[str, LevelDef]:
        """返回 (level_key, LevelDef)。

        ``key`` 为空时用 ``default_level``；没有默认值且只有一个 level 时用那个。
        其余情况抛 ``KeyError`` —— 让编译器变成一次明确错误，而不是猜一个。
        """
        if key:
            if key not in self.levels:
                raise KeyError(f"维度 {self.code} 没有口径 {key!r}，可选 {sorted(self.levels)}")
            return key, self.levels[key]
        if self.default_level and self.default_level in self.levels:
            return self.default_level, self.levels[self.default_level]
        if len(self.levels) == 1:
            only = next(iter(self.levels.items()))
            return only
        raise KeyError(f"维度 {self.code} 有多个口径但没有 default_level：{sorted(self.levels)}")


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    """``Intent.dimensions`` 里的一项：维度名 + 可选的口径限定。

    刻意与 :class:`DimensionDef` 分开：前者是**用户的说法**（经 L1 归一），
    后者是**配置的实体**。编译器的第一步就是把前者解析成后者。
    """

    name: str
    level: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
