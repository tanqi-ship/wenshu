-- 机构简称是整体上下文；产品展示过滤不能缩小权限内整体。
SELECT share_totals.prd_no, share_totals.prd_nm, share_totals.loan_balance,
       share_totals.total_value,
       1.0 * share_totals.loan_balance / NULLIF(share_totals.total_value, 0) AS share
FROM (
  SELECT share_groups.prd_no, share_groups.prd_nm, share_groups.loan_balance,
         SUM(share_groups.loan_balance) OVER () AS total_value
  FROM (
    SELECT t1.prd_no AS prd_no, MAX(t1.prd_nm) AS prd_nm,
           SUM(t1.current_balnc_cny) AS loan_balance
    FROM qa_loan_acc_detail AS t1
    WHERE t1.etl_dt = '2026-04-30'
      AND t1.org_no = 'B001'
      AND t1.org_no IN ('B001', 'B002')
      AND t1.prd_no IN ('LP01', 'LP03')
    GROUP BY t1.prd_no
  ) AS share_groups
) AS share_totals
WHERE share_totals.prd_no = 'LP03'
ORDER BY share_totals.prd_no;
