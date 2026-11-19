-- G-002 黄金 SQL：单指标 + 月末单时点 + 无维度（不带占位符的公式）
--
-- ★ 手写的，不是引擎产出的。
--
-- 这条刻意用「贷款余额」这个指标，因为它的公式**不带** {tbl} 占位符：
--
--     formula="SUM(current_balnc_cny)"        ← 语义层里就是这么写的
--
-- 编译器要负责把未限定的列补上表别名（compiler/metrics.py::qualify_columns）。
-- 所以这条黄金 SQL 同时钉住了两件事：
--   ① 最终形状对
--   ② 补别名的行为对（少了 t1. 前缀也算不一致）
--
-- 形状：month_end_single_point
-- 对应语义层：bank_mini.py 的 loan_balance
-- 对应评测用例：AQ-001（"2026年4月末全行贷款余额是多少？"，期望 20210000.00）
-- 对应规则：R-NAME-001 / R-PCT-001 都不触发

SELECT
  SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
WHERE
  t1.etl_dt = '2026-04-30'
