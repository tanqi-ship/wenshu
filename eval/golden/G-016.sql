-- 三个月各保留自身月份，UNION ALL 不去重、不按月重新求比率。
SELECT
  t1.stat_month AS stat_month,
  t1.prd_no,
  t1.prd_nm,
  t1.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202604 AS t1
WHERE t1.stat_month = '202604'
UNION ALL
SELECT
  t1_2.stat_month AS stat_month,
  t1_2.prd_no,
  t1_2.prd_nm,
  t1_2.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202605 AS t1_2
WHERE t1_2.stat_month = '202605'
UNION ALL
SELECT
  t1_3.stat_month AS stat_month,
  t1_3.prd_no,
  t1_3.prd_nm,
  t1_3.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202606 AS t1_3
WHERE t1_3.stat_month = '202606'
ORDER BY stat_month, prd_no;
