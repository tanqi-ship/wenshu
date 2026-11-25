SELECT SUM(CASE WHEN t1.fiv_lev_cla_flag IN ('30', '40', '50')
                THEN t1.current_balnc_cny ELSE 0 END)
       / NULLIF(SUM(t1.current_balnc_cny), 0) AS npl_ratio
FROM qa_loan_acc_detail AS t1
WHERE t1.etl_dt = '2026-04-30'
  AND t1.org_no = 'B001'
  AND t1.org_no IN ('B001', 'B002');
