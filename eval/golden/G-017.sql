-- 业务码值过滤与每张分表自身权限取交集；不同月份不共享策略列。
SELECT
  t1.stat_month AS stat_month,
  t1.prd_no,
  t1.prd_nm,
  t1.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202604 AS t1
WHERE t1.stat_month = '202604'
  AND t1.prd_no IN ('LP03', 'LP07')
  AND t1.npl_balance = '2850000'
UNION ALL
SELECT
  t1_2.stat_month AS stat_month,
  t1_2.prd_no,
  t1_2.prd_nm,
  t1_2.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202605 AS t1_2
WHERE t1_2.stat_month = '202605'
  AND t1_2.prd_no IN ('LP03', 'LP07')
  AND t1_2.prd_no = 'LP07'
ORDER BY stat_month, prd_no;
