-- G-001 黄金 SQL：单指标 + 月末单时点 + 无维度 + 无权限
--
-- ★ 这条是**手写**的，不是引擎产出的。
--   如果黄金答案也由引擎生成，那这个门禁只能证明"引擎没变"，
--   不能证明"引擎算对了"。所以本项目**不提供** --update / --bless。
--
-- 形状：month_end_single_point
--   SELECT SUM(CASE WHEN <分类> IN (...) THEN <余额> ELSE 0 END) AS <指标>
--   FROM <来源表> AS t1
--   WHERE t1.etl_dt = '<月末>'
--
-- 对应语义层：app/engine/semantic/bank_mini.py 的 npl_balance
-- 对应 fixture：qa_loan_acc_detail（见 fixtures/sql/bank_fixture.sql）
-- 对应评测：暂无完全对应的评测用例（AQ-002 带机构过滤，数字不同）
-- 对应规则：R-NAME-001（无维度，不触发）、R-PCT-001（非百分比，不触发）
--
-- ★ 写法必须与编译器产出一致：表别名 t1、日期用字符串字面量
--   （不用 DATE '...'）。这不是风格问题 —— 归一化只消掉排版和大小写，
--   不消除"语义等价但语法不同"的写法差异。

SELECT
  SUM(
    CASE
      WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
      THEN t1.current_balnc_cny
      ELSE 0
    END
  ) AS npl_balance
FROM qa_loan_acc_detail AS t1
WHERE
  t1.etl_dt = '2026-04-30'
