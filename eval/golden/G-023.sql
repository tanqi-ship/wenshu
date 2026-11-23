-- 登记简称精确选择本级编码，不扩展名称前缀或下辖网点。
SELECT SUM(t1.current_balnc_cny) AS loan_balance
FROM qa_loan_acc_detail AS t1
WHERE t1.etl_dt = '2026-04-30' AND t1.org_no = 'B001';
