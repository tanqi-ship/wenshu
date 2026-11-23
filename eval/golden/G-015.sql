-- AQ-006 标准五级分类口径；源表 PK 保证月份/产品唯一，预计算比例不再聚合。
SELECT
  t1.stat_month AS stat_month,
  t1.prd_no,
  t1.prd_nm,
  t1.npl_rate AS npl_ratio
FROM rpt_npl_by_product_202604 AS t1
WHERE t1.stat_month = '202604'
ORDER BY stat_month, prd_no;
