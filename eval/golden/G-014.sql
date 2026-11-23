-- LEFT 右表空权限不能仅放 ON，必须使整个查询 fail-closed。
SELECT t2.parent_org_no AS parent_org_no,
       SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
LEFT JOIN dim_org AS t2 ON t1.org_no = t2.org_no
WHERE t1.etl_dt = '2026-04-30' AND 1 = 0
GROUP BY t2.parent_org_no
