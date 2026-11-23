-- 两侧业务过滤与两表行级权限依来源绑定，权限保留声明顺序。
SELECT t2.parent_org_no AS parent_org_no,
       SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
INNER JOIN dim_org AS t2 ON t1.org_no = t2.org_no
WHERE t1.etl_dt = '2026-04-30'
  AND t1.org_no = 'B001'
  AND t2.parent_org_no IN ('F001', 'F002')
  AND t2.org_type = 'financial'
  AND t1.org_no IN ('B001', 'B002')
GROUP BY t2.parent_org_no
