"""可答性门禁（M4 实现）。

回答一个问题：**这次提问，语义层能支撑到什么程度？** 输出不是布尔值，而是三态：

    全部槽位可落地   -> COMPILE    走编译器
    部分槽位可落地   -> CLARIFY    只问缺的那一个
    关键槽位不可落地 -> REFUSE     拒答，且原因可解释

## 判据：语义层的显式存在性，不是向量距离

```
Intent.metric     在 metric 字典里（或其同义词里）吗？   -> 否则 UNKNOWN_METRIC
Intent.dimensions 每个维度在 dimension 字典里吗？         -> 否则 UNKNOWN_DIMENSION
Filter.value      在码值表 / 别名表里吗？                -> 否则 UNKNOWN_CODE
Intent.time_range 能被时间口径表解析吗？                 -> 否则 TIME_UNPARSABLE
指标来源表        ⊆ Scope.tables 吗？                    -> 否则 OUT_OF_SCOPE
```

这是可解释、可审计、换 embedding 模型不失效的（ADR-0002）。

**明确不采用** auto-ontology 的 `coverage = 命中实体/总实体`（其中"命中"= 向量距离小于阈值）：
阈值无法向审计解释、换模型即失效。向量检索在本项目里**只负责捞候选，不负责判定**。

## 输入输出

    输入：Intent + SemanticCatalog + Scope
    输出：Decision(kind=COMPILE|CLARIFY|REFUSE, problems=tuple[Problem, ...])

**与 validation 的关系**：两者都用 `Problem`，但职责不同——
coverage 判"能不能开始编译"，validation 判"编译结果对不对"。前者的 problem code
会直接变成给用户的文案（含权限类文案），后者的 code 主要用于评测聚合与重试。
"""

from __future__ import annotations

__all__: list[str] = []
