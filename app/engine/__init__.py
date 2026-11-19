"""wenshu 引擎。

分层（越靠下越"确定"）：

    semantic/     L0 语义层：指标、维度、口径、表关系、码值的领域模型
    clarify/      L1/L2 澄清：意图清洗 + 确定性槽位检查 + 追问预算
    coverage/     L2 门禁：可答性判定（语义层显式存在性）        ← M4
    compiler/     L3 编译：Intent + 语义层 + Scope -> AST -> SQL  ← 零模型
    validation/   L4 校验：只读、名字、范围、业务规则               ← 零模型

两条硬约束（见 docs/ARCHITECTURE.md）：

1. **只有 L1/L2 允许调用模型**；compiler 与 validation 必须是纯函数。
2. **validation 只接收 SQL 文本**，不接收 compiler 的中间产物——
   否则编译器的 bug 会被它自己的校验放过。
"""
