"""编译期错误。

编译失败是**确定性**的：同样的输入永远得到同样的失败（ADR-0005）。
因此错误必须精确到「谁该修」：

===============  ==========================================  ====================
stage            含义                                        谁该修
===============  ==========================================  ====================
``symbol_table`` 语义层里没有这个指标/维度/码值               配置维护者
``intent``       意图不完整（时间没解析出来、维度没定口径）     回 L2 澄清，问用户
``type_check``   指标与维度的组合非法                         配置维护者
``scope``        越权（指标来源表不在可见范围）                权限配置
``internal``     编译器自己的 bug                             本项目开发者
===============  ==========================================  ====================

**这个分类本身就是产出**：它把"为什么答不了"从一句模糊的失败，变成五个
可统计、可归因、可分别给不同人看的口径。
"""

from __future__ import annotations

from typing import Literal

CompileStage = Literal["symbol_table", "intent", "type_check", "scope", "internal"]


class CompileError(Exception):
    """编译失败。``code`` 稳定，可直接用于评测聚合。"""

    def __init__(self, stage: CompileStage, code: str, message: str) -> None:
        super().__init__(message)
        self.stage: CompileStage = stage
        self.code: str = code
        self.message: str = message

    def __str__(self) -> str:
        return f"[{self.stage}/{self.code}] {self.message}"


# -- 稳定错误码 -------------------------------------------------------------
# symbol_table：语义层覆盖不足
UNKNOWN_METRIC = "UNKNOWN_METRIC"
UNKNOWN_DIMENSION = "UNKNOWN_DIMENSION"
UNKNOWN_CODE = "UNKNOWN_CODE"
UNKNOWN_SOURCE = "UNKNOWN_SOURCE"

# intent：意图不完整，应澄清
TIME_UNRESOLVED = "TIME_UNRESOLVED"
TIME_UNSUPPORTED = "TIME_UNSUPPORTED"
DIMENSION_LEVEL_UNRESOLVED = "DIMENSION_LEVEL_UNRESOLVED"

# intent：多时点对比的意图不对（M2.1）。三条都必须**明确报错**，不许静默忽略 ——
# 多时点一旦被当成单时点处理，SQL 会变成"把两个时点加在一起"，跑得通、数字还挺像。
MULTI_POINT_WITHOUT_COMPARISON = "MULTI_POINT_WITHOUT_COMPARISON"
COMPARISON_UNSUPPORTED = "COMPARISON_UNSUPPORTED"
COMPARISON_POINT_COUNT = "COMPARISON_POINT_COUNT"

# intent：每月 Top N（M2.2）
TOPN_INVALID_N = "TOPN_INVALID_N"
TOPN_UNSUPPORTED = "TOPN_UNSUPPORTED"
TOPN_DIMENSION_REQUIRED = "TOPN_DIMENSION_REQUIRED"
TOPN_TIME_UNSUPPORTED = "TOPN_TIME_UNSUPPORTED"

# type_check：配置组合非法
TOPN_OUTPUT_COLLISION = "TOPN_OUTPUT_COLLISION"
DIMENSION_NOT_ALLOWED = "DIMENSION_NOT_ALLOWED"
# 指标公式不是"可加总"形状（顶层单个 SUM），无法做差值 —— M2.1 的显式边界。
# 谁来修：语义层维护者（换成可加总口径）或产品（这个指标暂不支持对比）。
DELTA_NOT_ADDITIVE = "DELTA_NOT_ADDITIVE"

# scope：越权
METRIC_OUT_OF_SCOPE = "METRIC_OUT_OF_SCOPE"

# internal：编译器 bug
FORMULA_INVALID = "FORMULA_INVALID"
AST_INVALID = "AST_INVALID"

# M2.3：受限双表关联。每条失败都是确定性的，不能丢掉第二张表继续查。
JOIN_RELATION_MISSING = "JOIN_RELATION_MISSING"
JOIN_RELATION_AMBIGUOUS = "JOIN_RELATION_AMBIGUOUS"
JOIN_TABLE_LIMIT = "JOIN_TABLE_LIMIT"
JOIN_TYPE_UNSUPPORTED = "JOIN_TYPE_UNSUPPORTED"
JOIN_CARDINALITY_UNSAFE = "JOIN_CARDINALITY_UNSAFE"
JOIN_SHAPE_UNSUPPORTED = "JOIN_SHAPE_UNSUPPORTED"
JOIN_FORMULA_UNSUPPORTED = "JOIN_FORMULA_UNSUPPORTED"
JOIN_FILTER_UNSUPPORTED = "JOIN_FILTER_UNSUPPORTED"
JOIN_OUTPUT_COLLISION = "JOIN_OUTPUT_COLLISION"
TABLE_NOT_IN_SCOPE = "TABLE_NOT_IN_SCOPE"
COLUMN_NOT_IN_SCOPE = "COLUMN_NOT_IN_SCOPE"

# M2.4：预计算报表只按显式登记的月份与粒度读取。
REPORT_SOURCE_MISSING = "REPORT_SOURCE_MISSING"
REPORT_PARTITION_MISSING = "REPORT_PARTITION_MISSING"
REPORT_CONFIG_INVALID = "REPORT_CONFIG_INVALID"
REPORT_GRAIN_MISMATCH = "REPORT_GRAIN_MISMATCH"
REPORT_TIME_UNSUPPORTED = "REPORT_TIME_UNSUPPORTED"
REPORT_SHAPE_UNSUPPORTED = "REPORT_SHAPE_UNSUPPORTED"
REPORT_FILTER_UNSUPPORTED = "REPORT_FILTER_UNSUPPORTED"

# M2.5：同口径整体分母；展示过滤不能缩小整体，行权限不能被移走。
SHARE_SHAPE_UNSUPPORTED = "SHARE_SHAPE_UNSUPPORTED"
SHARE_DIMENSION_REQUIRED = "SHARE_DIMENSION_REQUIRED"
SHARE_DIMENSION_MISMATCH = "SHARE_DIMENSION_MISMATCH"
SHARE_TIME_UNSUPPORTED = "SHARE_TIME_UNSUPPORTED"
SHARE_CONTEXT_UNRESOLVED = "SHARE_CONTEXT_UNRESOLVED"
SHARE_FILTER_UNSUPPORTED = "SHARE_FILTER_UNSUPPORTED"
SHARE_METRIC_NOT_ADDITIVE = "SHARE_METRIC_NOT_ADDITIVE"
SHARE_OUTPUT_COLLISION = "SHARE_OUTPUT_COLLISION"
