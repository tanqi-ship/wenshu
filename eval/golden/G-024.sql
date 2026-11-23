-- 名称/编码混合输入均转为精确编码；名称只作展示。
SELECT t1.org_no AS org_no, MAX(t1.org_nm) AS org_nm,
       SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
WHERE t1.etl_dt = '2026-04-30' AND t1.org_no IN ('B001', 'B002')
GROUP BY t1.org_no;
