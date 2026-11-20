-- G-008 黄金 SQL：双时点对比 delta（贷款余额，2026-04-30 相对 2026-01-31）
--
-- ★ 这条是**手写**的，不是引擎产出的。形状与 eval/cases 的 AQ-004 对齐。
--
-- 形状：comparison_delta
--   SELECT SUM(CASE WHEN etl_dt = '<cur>'  THEN <arg> ELSE 0 END) AS cur_balance,
--          SUM(CASE WHEN etl_dt = '<base>' THEN <arg> ELSE 0 END) AS base_balance,
--          SUM(CASE WHEN etl_dt = '<cur>'  THEN <arg> ELSE 0 END)
--        - SUM(CASE WHEN etl_dt = '<base>' THEN <arg> ELSE 0 END) AS delta
--   FROM <来源表> AS t1
--   WHERE etl_dt IN ('<cur>', '<base>')
--
-- 四个刻意的写法：
--
--   ① **一个 SELECT 一次筛出两个时点**（WHERE ... IN），绝不用标量子查询
--      去查基准时点（规则 R-COMP-001 / C-05）。子查询写法在数据缺失时会静默
--      返回 NULL 或 0，数字看上去居然还有模有样。
--
--   ② **差值重复写了两遍完整表达式**，不是 ``cur_balance - base_balance``。
--      后者在任何数据库都不合法：同一个 SELECT 里的表达式不能引用本层别名。
--      重复写法让 SQL 变长约 3 倍，这是刻意付的代价 —— 换来"对比查询里没有子查询"
--      这件事一眼可见。
--
--   ③ **三列都要返回**，不能只返回 delta。排序、画图、写报告都需要当前值和基准值
--      （AQ-004 第③条：派生结果不是物理字段）。
--
--   ④ 时点顺序是 **cur 在前、base 在后**，与 WHERE 里 IN 的顺序一致。
--      顺序反了 SQL 依然能跑，只是 delta 的符号全反 —— 这类错最难看出来。
--
-- ⚠️ 口径提醒（重要）：``ELSE 0`` 让缺失时点当作 0 参与计算。
--      数学上这是"分段求和"的正确写法，但业务上**不等于承认"缺失快照就是零"**：
--      fixture 里 D-10 就有"ETL 失败导致某月没有快照"的场景，那时 delta 会算成
--      "当前值 - 0"，看起来是"大幅增加"。要不要拒答、要不要标注，属于 L2 门禁
--      和答案披露的职责，不在编译器里决定。
--
-- 对应语义层：bank_mini.py 的 loan_balance（公式不带 {tbl} 占位符）
-- 对应 fixture：qa_loan_acc_detail
-- 对应评测：AQ-004（2026年1月末到4月末，全行贷款余额增加额，期望 1,010,000.00）
-- 对应规则：C-05 / R-COMP-001（多时点必须一次筛出，禁止子查询）

SELECT
  SUM(CASE WHEN t1.etl_dt = '2026-04-30' THEN t1.current_balnc_cny ELSE 0 END) AS cur_balance,
  SUM(CASE WHEN t1.etl_dt = '2026-01-31' THEN t1.current_balnc_cny ELSE 0 END) AS base_balance,
  SUM(CASE WHEN t1.etl_dt = '2026-04-30' THEN t1.current_balnc_cny ELSE 0 END)
    - SUM(CASE WHEN t1.etl_dt = '2026-01-31' THEN t1.current_balnc_cny ELSE 0 END) AS delta
FROM qa_loan_acc_detail AS t1
WHERE
  t1.etl_dt IN ('2026-04-30', '2026-01-31')
