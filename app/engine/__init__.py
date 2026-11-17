"""wenshu 引擎。

分层（越靠上越"确定"）：

    semantic/    语义层：指标、维度、口径、表关系的领域模型
    retrieval/   检索：按业务词找表/列/指标，决定本次表范围（软范围）
    compiler/    编译器：Intent -> sqlglot AST -> 方言 SQL（零模型）
    validation/  校验：只读、名字、业务规则（把 prompt 里的规则变成代码）
    clarify/     澄清：确定性槽位检查 + 追问预算

与 app/legacy/ 的关系：legacy 是从 askdb 搬来并**将逐步被替换**的实现。
新逻辑只写在这里，不要写进 legacy。
"""
