"""编译层（待实现）。

核心思想：**把"写 SQL"从模型手里拿走。**

模型输出 ``Intent``（业务语言），编译器负责：

    Intent + 语义层 + 权限
        -> sqlglot AST
        -> 方言 SQL

全程零模型调用。好处不只是省 token：

* **可单测**：给定 Intent，断言生成的 AST 结构，不需要跑模型
* **口径一致**：同一个指标永远展开成同一个表达式，不会这次乘 100 下次不乘
* **时间口径集中**："近半年"只有一个定义，不在 5,500 字 prompt 里各说一遍
* **错误提前**：编译不出唯一 AST 时说明意图有歧义，此时应该澄清，
  而不是让模型写一条看起来能跑的 SQL

首批要覆盖的展开：

1. 指标公式（``MetricDef.formula``）
2. 时间条件（``TimeRange.grain``：month_end / month / range / 多时点对比）
3. 维度分组 + 名称字段用 MAX()
4. Top N（含每月 Top N 的窗口函数）
5. 行级权限 WHERE 注入（复用 legacy 的 ``row_policy_service``）
"""
