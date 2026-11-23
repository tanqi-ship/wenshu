-- 每月 Top 1：先聚合，再逐月排名，最后筛排名；名称不参与分组。
SELECT ranked.etl_dt, ranked.org_no, ranked.org_nm, ranked.loan_balance
FROM (
    SELECT monthly.etl_dt, monthly.org_no, monthly.org_nm, monthly.loan_balance,
           ROW_NUMBER() OVER (
               PARTITION BY monthly.etl_dt
               ORDER BY monthly.loan_balance DESC, monthly.org_no ASC
           ) AS rn
    FROM (
        SELECT t1.etl_dt AS etl_dt,
               t1.org_no AS org_no,
               MAX(t1.org_nm) AS org_nm,
               SUM(t1.current_balnc_cny) AS loan_balance
        FROM qa_loan_acc_detail AS t1
        WHERE t1.etl_dt IN ('2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30')
        GROUP BY t1.etl_dt, t1.org_no
    ) AS monthly
) AS ranked
WHERE ranked.rn <= 1
ORDER BY ranked.etl_dt, ranked.rn
