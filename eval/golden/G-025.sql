-- 上级维度单独解析并绑定；直接 JOIN 等值键和授权交集不变。
SELECT t2.parent_org_no AS parent_org_no, SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
INNER JOIN dim_org AS t2 ON t1.org_no = t2.org_no
WHERE t1.etl_dt = '2026-04-30'
  AND t1.org_no = 'O001'
  AND t2.parent_org_no = 'B001'
  AND t1.org_no IN ('O001', 'O002')
GROUP BY t2.parent_org_no;
