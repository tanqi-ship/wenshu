-- INNER：上级机构来自维表；指标来自贷款快照，不因同机构多笔贷款而重复关联。
SELECT t2.parent_org_no AS parent_org_no,
       SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
INNER JOIN dim_org AS t2 ON t1.org_no = t2.org_no
WHERE t1.etl_dt = '2026-04-30'
GROUP BY t2.parent_org_no
