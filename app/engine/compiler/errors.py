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

# type_check：配置组合非法
DIMENSION_NOT_ALLOWED = "DIMENSION_NOT_ALLOWED"

# scope：越权
METRIC_OUT_OF_SCOPE = "METRIC_OUT_OF_SCOPE"

# internal：编译器 bug
FORMULA_INVALID = "FORMULA_INVALID"
AST_INVALID = "AST_INVALID"
