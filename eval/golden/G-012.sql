-- LEFT：没有维表匹配的主表贷款保留在 NULL 分组；不自动补名或回退机构。
SELECT t2.parent_org_no AS parent_org_no,
       SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
LEFT JOIN dim_org AS t2 ON t1.org_no = t2.org_no
WHERE t1.etl_dt = '2026-04-30'
GROUP BY t2.parent_org_no
