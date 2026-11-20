-- G-007 黄金 SQL：行级策略**没有取值** → 恒假条件（fail-closed）
--
-- ★ 这条是**手写**的，不是引擎产出的。
--
-- 形状：row_policy_fail_closed
--   SELECT <指标表达式> AS <指标 code>
--   FROM <来源表> AS t1
--   WHERE t1.etl_dt = '<月末>' AND 1 = 0
--
-- 这是整个仓库里**最重要的一条 SQL**，因为它钉的是"出错时默认拒绝"。
--
-- 场景：策略已经配上了（说明确实限了权限），但取值集合是空的 ——
-- 可能是权限数据没同步、可能是上游查角色失败了。此时两种实现的区别是：
--
--   静默跳过这条策略  → SQL 里没有权限条件 → **用户看到了全行数据**
--   生成恒假条件      → SQL 里 1 = 0        → 用户什么都看不到（正确）
--
-- 前者不报错、不告警、跑得还挺快，是典型"看起来一切正常"的越权事故。
-- 所以编译器选择 fail-closed：宁可查不出数，也不多给一行。
--
-- 两件事顺带说明：
--
--   ① ``1 = 0`` 是当前实现对"恒假"的编码方式。想换成 ``FALSE`` 之类的等价写法
--      是允许的，但那是**改变形状契约**，必须同时改这条黄金 SQL ——
--      这正是黄金集该有的阻力。
--   ② 时间条件依然在。fail-closed **不是**"整个 WHERE 变成 1 = 0"，
--      而是"多一个恒假合取项"。两者结果一样，但后者保留了可读的审计痕迹：
--      看 SQL 就知道这次查的是哪个时点、只是被权限挡住了。
--
-- 对应语义层：bank_mini.py 的 npl_balance
-- 对应 fixture：qa_loan_acc_detail
-- 对应评测：暂无（评测集里没有带权限的用例）
-- 对应规则：ADR-0004（fail-closed）；test_row_policy_without_values_is_fail_closed

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
  AND 1 = 0
