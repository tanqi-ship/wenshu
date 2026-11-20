-- G-004 黄金 SQL：单指标 + 月末单时点 + 业务过滤（机构 = 渝北支行）
--
-- ★ 这条是**手写**的，不是引擎产出的。
--
-- 形状：business_filter_eq
--   SELECT <指标表达式> AS <指标 code>
--   FROM <来源表> AS t1
--   WHERE t1.etl_dt = '<月末>' AND t1.org_no = '<机构存储值>'
--
-- 这条钉住的是**值翻译**，也就是 ADR-0002 里"值级映射只能精确等值"的那条：
--
--   Intent 里写的是业务语言「渝北支行」（filters[].value），
--   而 SQL 里必须是数据库真实存储值 'B001'（code_map 负责翻译）。
--
--   ★ 为什么这一条特别容易错：如果翻译没发生，SQL 会变成
--     `t1.org_no = '渝北支行'` —— 语法完全合法、L4 六条规则全绿、
--     甚至能跑出结果，但结果恒为 0 行。**没有任何东西会报错。**
--     只有黄金集能发现它。
--
--   同理，这里**不能**写成 LIKE '%渝北%' / 前缀匹配 / IN 一堆近似值：
--   机构名是高基数字段（D-06），模糊匹配会把"渝北支行营业部"之类的
--   下级机构一起卷进来，数字变大且看不出来。
--
-- 对应语义层：bank_mini.py 的 npl_balance + code_map["org"]["渝北支行"] = "B001"
-- 对应 fixture：qa_loan_acc_detail（B001 只有 3 笔：LN0001/LN0002/LN0005）
-- 对应评测：AQ-002（2026年4月末渝北支行不良贷款余额）
-- 对应规则：D-01（org_no 与 accounting_org_no 冗余）/ D-06（高基数名称必须等值）

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
  t1.etl_dt = '2026-04-30' AND t1.org_no = 'B001'
