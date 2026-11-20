-- G-006 黄金 SQL：单指标 + 月末单时点 + **行级权限注入**（可见 B001/B002）
--
-- ★ 这条是**手写**的，不是引擎产出的。
--
-- 形状：row_policy_injection
--   SELECT <指标表达式> AS <指标 code>
--   FROM <来源表> AS t1
--   WHERE t1.etl_dt = '<月末>' AND t1.org_no IN ('<值1>', '<值2>')
--
-- 这条用例与其它用例的**根本区别**：差异不来自 Intent，而来自编译器的
-- 第三个输入 Scope（ADR-0004）。Intent 完全一样，换个身份就得换条 SQL。
--
-- 钉住三件事：
--
--   ① **权限条件是 AND 上去的，不是覆盖时间条件。** 常见实现错误是
--      "把权限当成 WHERE 的唯一来源"，于是丢了 etl_dt，数字变成跨月累加。
--
--   ② **权限条件排在业务条件之后。** 合成顺序固定为 时间 → 业务 → 权限
--      （compiler/__init__.py::_combine）。顺序固定不是洁癖：下游任何一次
--      "改条件重跑"都必须能认定"最后那一段是权限、不许动"。
--      askdb 曾在这里出过事故，靠 rebuild_sql_service.py 打补丁才修好。
--
--   ③ 两个取值用 IN，单值用 = —— 形状由取值个数决定，不由调用方选择。
--
-- 对应语义层：bank_mini.py 的 npl_balance
-- 对应 fixture：qa_loan_acc_detail（org_no 只有 B001/B002 两种）
-- 对应评测：暂无（评测集里没有带权限的用例）
-- 对应规则：ADR-0004（权限做输入不做系统）；R-SCOPE-001（待实现，见 ROADMAP 2.x）

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
  t1.etl_dt = '2026-04-30' AND t1.org_no IN ('B001', 'B002')
