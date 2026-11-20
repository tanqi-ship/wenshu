-- G-003 黄金 SQL：单指标 + 月末单时点 + 维度分组（机构 / 财务口径）
--
-- ★ 这条是**手写**的，不是引擎产出的。
--   如果黄金答案也由引擎生成，那这个门禁只能证明"引擎没变"，
--   不能证明"引擎算对了"。所以本项目**不提供** --update / --bless。
--
-- 形状：dimension_grouping
--   SELECT <编号列> AS <编号列>,
--          MAX(<名称列>) AS <名称列>,
--          <指标表达式> AS <指标 code>
--   FROM <来源表> AS t1
--   WHERE t1.etl_dt = '<月末>'
--   GROUP BY <编号列>
--
-- 三个刻意的写法，每一个都在钉一条规则：
--   ① GROUP BY 里只有 org_no（编号列）。名称列 org_nm **绝不**进 GROUP BY ——
--      重名会把同一实体拆成多行（R-NAME-001 / C-11）。要展示名称只能用 MAX()。
--   ② 机构用 org_no 而不是 accounting_org_no：默认是财务口径（D-01）。
--      管户口径要显式写 customer_management level，不是靠猜。
--   ③ 名称列的 MAX() 与指标列是**兄弟投影**，不是子查询 —— 一次扫描出结果。
--
-- 对应语义层：app/engine/semantic/bank_mini.py 的 loan_balance + org 维度
-- 对应 fixture：qa_loan_acc_detail（见 fixtures/sql/bank_fixture.sql）
-- 对应评测：AQ-005（各信贷产品的贷款余额，维度不同但形状同族）
-- 对应规则：R-NAME-001 / C-11

SELECT
  t1.org_no AS org_no,
  MAX(t1.org_nm) AS org_nm,
  SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
WHERE
  t1.etl_dt = '2026-04-30'
GROUP BY
  t1.org_no
