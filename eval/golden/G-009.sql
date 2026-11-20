-- G-009 黄金 SQL：双时点对比 delta（不良贷款余额，公式内部自带 CASE）
--
-- ★ 这条是**手写**的，不是引擎产出的。
--
-- 为什么要这条（G-008 已经覆盖了 delta 的基本形状）：不良余额的公式是
-- ``SUM(CASE WHEN 五级分类 IN ('30','40','50') THEN 余额 ELSE 0 END)``，
-- 也就是说"聚合的参数本身就是一个 CASE"。时点条件必须注入到**聚合参数内部**，
-- 于是产出两层嵌套 CASE：
--
--   SUM(CASE WHEN etl_dt = '当期'  THEN (CASE WHEN 分类 IN (...) THEN 余额 ELSE 0 END) ELSE 0 END)
--
-- 如果实现把时点条件套在聚合**外面**（`CASE WHEN etl_dt = ... THEN SUM(...) ELSE 0 END`），
-- 那条 SQL 语法同样合法、也能跑，但语义完全错：SUM 会在整张表上先聚合完，
-- 再由 CASE 决定要不要它。这类错只有对照黄金 SQL 才能发现 —— 因为
-- 结果是**一行一份**，不报错、不告警。
--
-- 另外它钉住"什么指标能做 delta"：顶层是单个 SUM 才可加总。
-- 不良**率**（npl_ratio，顶层是除法）在这条路上会被明确拒绝（DELTA_NOT_ADDITIVE），
-- 因为"分段比率相减"没有业务含义。
--
-- 对应语义层：bank_mini.py 的 npl_balance
-- 对应 fixture：qa_loan_acc_detail
-- 对应评测：无（AQ-004 用的是贷款余额，不是不良余额）
-- 对应规则：C-05 / R-COMP-001；R-NAME-001（无维度，不触发）

SELECT
  SUM(
    CASE
      WHEN t1.etl_dt = '2026-04-30'
      THEN CASE
        WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
        THEN t1.current_balnc_cny
        ELSE 0
      END
      ELSE 0
    END
  ) AS cur_balance,
  SUM(
    CASE
      WHEN t1.etl_dt = '2026-01-31'
      THEN CASE
        WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
        THEN t1.current_balnc_cny
        ELSE 0
      END
      ELSE 0
    END
  ) AS base_balance,
  SUM(
    CASE
      WHEN t1.etl_dt = '2026-04-30'
      THEN CASE
        WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
        THEN t1.current_balnc_cny
        ELSE 0
      END
      ELSE 0
    END
  )
    - SUM(
      CASE
        WHEN t1.etl_dt = '2026-01-31'
        THEN CASE
          WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
          THEN t1.current_balnc_cny
          ELSE 0
        END
        ELSE 0
      END
    ) AS delta
FROM qa_loan_acc_detail AS t1
WHERE
  t1.etl_dt IN ('2026-04-30', '2026-01-31')
