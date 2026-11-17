-- =============================================================================
--  wenshu 假银行 fixture 库（目标业务库）
--
--  设计原则：**故意的"脏"。**
--
--  askdb 的回归 fixture 是 3 张表、27 个字段全有中文注释、没有历史垃圾。
--  用它开发会让人误以为系统做对了 —— 因为真实银行库的每一个坑它都没有。
--
--  所以这里的每一处缺陷都是刻意的，而且每一处都对应一个已知的失败模式。
--  文件末尾附了缺陷清单（编号 D-01 ~ D-09），评测用例会按编号引用。
--
--  方言：PostgreSQL（本地开发）。生产是 Oracle，差异点见文件末尾。
-- =============================================================================


-- ===========================================================================
--  明细表：字段冗余 + 缺注释 + NULL 语义不明
-- ===========================================================================
DROP TABLE IF EXISTS qa_loan_acc_detail;
CREATE TABLE qa_loan_acc_detail (
    etl_dt                date           NOT NULL,          -- 月末快照
    borrow_note_no        varchar(40)    NOT NULL,          -- 借据号
    cust_no               varchar(40),
    cust_nm               varchar(100),
    org_no                varchar(20),                      -- 财务本级机构
    org_nm                varchar(100),                     -- D-01 与 accounting_org_no 冗余
    accounting_org_no     varchar(20),                      -- D-01 有 5% 的行与 org_no 不同
    higher_level_org_no   varchar(20),                      -- 财务上级机构
    higher_level_org_nm   varchar(100),
    belong_to_org_no      varchar(20),                      -- 管户网点（D-06 高基数）
    belong_to_org_nm      varchar(100),
    credit_loan_prodct_no varchar(20),
    credit_loan_prodct_nm varchar(100),                     -- D-07 名称/编码双字段
    fiv_lev_cla_flag      varchar(10),                      -- 五级分类 10/20/30/40/50
    overdue_day           integer,                          -- D-09 NULL 语义不明
    current_balnc_cny     numeric(20,2),                    -- 贷款余额
    credit_loan_balnc     numeric(20,2),                    -- D-02 与 current_balnc_cny 口径不同
    ead                   numeric(20,2),
    ecl_final             numeric(20,2),
    some_field_xx         varchar(20),                      -- D-03 遗留字段，无任何描述
    CONSTRAINT pk_qa_loan_acc PRIMARY KEY (etl_dt, borrow_note_no)
);

-- 刻意只给一部分字段加注释（约 70%），D-03：真实库里 30% 的列没有描述
COMMENT ON TABLE  qa_loan_acc_detail IS '贷款账户明细（月末快照）';
COMMENT ON COLUMN qa_loan_acc_detail.etl_dt IS 'ETL 快照日期，取月末';
COMMENT ON COLUMN qa_loan_acc_detail.borrow_note_no IS '借据号';
COMMENT ON COLUMN qa_loan_acc_detail.org_no IS '财务本级机构号';
COMMENT ON COLUMN qa_loan_acc_detail.org_nm IS '机构名称';
COMMENT ON COLUMN qa_loan_acc_detail.higher_level_org_no IS '财务上级机构号';
COMMENT ON COLUMN qa_loan_acc_detail.belong_to_org_no IS '管户网点号';
COMMENT ON COLUMN qa_loan_acc_detail.credit_loan_prodct_no IS '信贷产品编码';
COMMENT ON COLUMN qa_loan_acc_detail.fiv_lev_cla_flag IS '五级分类：10正常 20关注 30次级 40可疑 50损失';
COMMENT ON COLUMN qa_loan_acc_detail.current_balnc_cny IS '贷款余额（人民币元）';
COMMENT ON COLUMN qa_loan_acc_detail.ead IS '违约风险暴露';
COMMENT ON COLUMN qa_loan_acc_detail.ecl_final IS '预期信用损失（最终）';
-- 未加注释的（真实库里就是这样）：cust_no, cust_nm, accounting_org_no,
-- higher_level_org_nm, belong_to_org_nm, credit_loan_prodct_nm,
-- overdue_day, credit_loan_balnc, some_field_xx


-- ===========================================================================
--  核销表：与明细表通过"来源账户 = 借据号"关联
-- ===========================================================================
DROP TABLE IF EXISTS qa_writeoff_detail;
CREATE TABLE qa_writeoff_detail (
    writeoff_id        bigint         NOT NULL,
    source_account_no  varchar(40),                      -- 关联 qa_loan_acc_detail.borrow_note_no
    writeoff_date      date,
    writeoff_principal numeric(20,2),                    -- 核销本金
    writeoff_balance   numeric(20,2),                    -- 核销余额（D-02 与本金是两个口径）
    recovered_amount   numeric(20,2),                    -- 已收回
    CONSTRAINT pk_qa_writeoff PRIMARY KEY (writeoff_id)
);
COMMENT ON TABLE  qa_writeoff_detail IS '核销明细';
COMMENT ON COLUMN qa_writeoff_detail.writeoff_principal IS '核销本金';
COMMENT ON COLUMN qa_writeoff_detail.writeoff_balance IS '核销余额';


-- ===========================================================================
--  机构维度表：自引用层级（测试递归 CTE）
-- ===========================================================================
DROP TABLE IF EXISTS dim_org;
CREATE TABLE dim_org (
    org_no         varchar(20) NOT NULL,
    org_nm         varchar(100),
    parent_org_no  varchar(20),                          -- D-08 自引用
    org_level      integer,                              -- 1总行 2分行 3支行 4网点
    org_type       varchar(20),                          -- financial / customer
    CONSTRAINT pk_dim_org PRIMARY KEY (org_no)
);
COMMENT ON TABLE  dim_org IS '机构维度（自引用层级）';


-- ===========================================================================
--  报表表家族：同一套 schema 按月分表（测试表家族识别 + UNION ALL）
-- ===========================================================================
DROP TABLE IF EXISTS rpt_npl_by_product_202604;
CREATE TABLE rpt_npl_by_product_202604 (
    stat_month  varchar(6) NOT NULL,                     -- '202604'
    prd_no      varchar(20) NOT NULL,
    prd_nm      varchar(100),
    npl_balance numeric(20,2),
    npl_rate    numeric(10,6),                           -- 0~1 小数，标准口径
    CONSTRAINT pk_rpt_npl_202604 PRIMARY KEY (stat_month, prd_no)
);

DROP TABLE IF EXISTS rpt_npl_by_product_202605;
CREATE TABLE rpt_npl_by_product_202605 (LIKE rpt_npl_by_product_202604 INCLUDING ALL);

DROP TABLE IF EXISTS rpt_npl_by_product_202606;
CREATE TABLE rpt_npl_by_product_202606 (LIKE rpt_npl_by_product_202604 INCLUDING ALL);

-- D-04 混口径：另一张报表的"npl_rate"是按逾期天数算的，不是按五级分类。
--        两张表字段同名、含义不同 —— 这正是场景/域隔离要挡的东西。
DROP TABLE IF EXISTS rpt_npl_by_overdue_202604;
CREATE TABLE rpt_npl_by_overdue_202604 (
    stat_month  varchar(6) NOT NULL,
    prd_no      varchar(20) NOT NULL,
    prd_nm      varchar(100),
    npl_balance numeric(20,2),
    npl_rate    numeric(10,6),                           -- 按逾期 90 天以上计算
    CONSTRAINT pk_rpt_od_202604 PRIMARY KEY (stat_month, prd_no)
);
COMMENT ON COLUMN rpt_npl_by_overdue_202604.npl_rate IS '不良率（按逾期天数口径，非五级分类口径）';


-- ===========================================================================
--  视图：跨表加工（测试视图处理）
-- ===========================================================================
CREATE OR REPLACE VIEW v_loan_wide AS
SELECT d.etl_dt,
       d.borrow_note_no,
       d.org_no,
       o.org_nm AS parent_org_nm,
       d.credit_loan_prodct_no,
       d.current_balnc_cny,
       CASE WHEN d.fiv_lev_cla_flag IN ('30','40','50')
            THEN d.current_balnc_cny ELSE 0 END AS npl_balnc
  FROM qa_loan_acc_detail d
  LEFT JOIN dim_org o ON o.org_no = d.higher_level_org_no;


-- ===========================================================================
--  历史垃圾：废弃表 / 临时备份（D-05）
--  真实银行库里这类表非常常见，选表阶段必须能把它们排除掉。
-- ===========================================================================
DROP TABLE IF EXISTS dm_loan_old_2023;
CREATE TABLE dm_loan_old_2023 (
    stat_dt       date,
    org_cd        varchar(20),
    loan_amt      numeric(20,2),
    remark        varchar(500)
);
COMMENT ON TABLE dm_loan_old_2023 IS '2023 年旧版贷款集市，已停用，勿用';

DROP TABLE IF EXISTS tmp_loan_bak_0725;
CREATE TABLE tmp_loan_bak_0725 (LIKE qa_loan_acc_detail INCLUDING ALL);
COMMENT ON TABLE tmp_loan_bak_0725 IS '某人 07-25 备份，已废弃';


-- ===========================================================================
--  数据：3 个月末快照（刻意缺 2026-03，D-10 ETL 失败过）
-- ===========================================================================
INSERT INTO dim_org (org_no, org_nm, parent_org_no, org_level, org_type) VALUES
  ('H000', '总行',       NULL,   1, 'financial'),
  ('B001', '渝北支行',   'H000', 3, 'financial'),
  ('B002', '江北支行',   'H000', 3, 'financial'),
  ('O001', '渝北龙溪分理处', 'B001', 4, 'customer'),
  ('O002', '渝北回兴储蓄所', 'B001', 4, 'customer'),
  ('O003', '江北观音桥营业部', 'B002', 4, 'customer');

-- 2026-01-31
INSERT INTO qa_loan_acc_detail VALUES
  ('2026-01-31','LN0001','C001','重庆甲公司','B001','渝北支行','B001','H000','总行','O001','渝北龙溪分理处','LP01','个人住房贷款','10', 0,   8000000.00, 7900000.00, 8000000.00,  40000.00, NULL),
  ('2026-01-31','LN0002','C002','重庆乙公司','B001','渝北支行','B001','H000','总行','O002','渝北回兴储蓄所','LP03','小微企业贷款','30',120,  3000000.00, 2900000.00, 3000000.00, 900000.00, NULL),
  ('2026-01-31','LN0003','C003','重庆丙公司','B002','江北支行','B002','H000','总行','O003','江北观音桥营业部','LP05','流动资金贷款','20',15, 5000000.00, 5000000.00, 5000000.00, 150000.00, NULL),
  ('2026-01-31','LN0004','C004','重庆丁公司','B002','江北支行','B002','H000','总行',NULL,NULL,'LP07','消费贷款','40',360, 1200000.00, 1200000.00, 1200000.00, 540000.00, NULL),
  ('2026-01-31','LN0005','C005','重庆戊公司','B001','渝北支行',NULL,NULL,NULL,NULL,NULL,'LP01','个人住房贷款','10',NULL, 2000000.00, 2000000.00, 2000000.00, 10000.00, NULL);

-- 2026-02-28
INSERT INTO qa_loan_acc_detail VALUES
  ('2026-02-28','LN0001','C001','重庆甲公司','B001','渝北支行','B001','H000','总行','O001','渝北龙溪分理处','LP01','个人住房贷款','10', 0,   7950000.00, 7850000.00, 7950000.00,  39750.00, NULL),
  ('2026-02-28','LN0002','C002','重庆乙公司','B001','渝北支行','B001','H000','总行','O002','渝北回兴储蓄所','LP03','小微企业贷款','40',210,  2950000.00, 2850000.00, 2950000.00, 1180000.00, NULL),
  ('2026-02-28','LN0003','C003','重庆丙公司','B002','江北支行','B002','H000','总行','O003','江北观音桥营业部','LP05','流动资金贷款','10', 0,  5000000.00, 5000000.00, 5000000.00, 150000.00, NULL),
  ('2026-02-28','LN0004','C004','重庆丁公司','B002','江北支行','B002','H000','总行',NULL,NULL,'LP07','消费贷款','50',400, 1150000.00, 1150000.00, 1150000.00, 575000.00, NULL),
  ('2026-02-28','LN0005','C005','重庆戊公司','B001','渝北支行',NULL,NULL,NULL,NULL,NULL,'LP01','个人住房贷款','10',NULL, 1980000.00, 1980000.00, 1980000.00, 9900.00, NULL);

-- 2026-04-30（缺 2026-03-31：D-10）
INSERT INTO qa_loan_acc_detail VALUES
  ('2026-04-30','LN0001','C001','重庆甲公司','B001','渝北支行','B001','H000','总行','O001','渝北龙溪分理处','LP01','个人住房贷款','10', 0,   7850000.00, 7750000.00, 7850000.00,  39250.00, NULL),
  ('2026-04-30','LN0002','C002','重庆乙公司','B001','渝北支行','B001','H000','总行','O002','渝北回兴储蓄所','LP03','小微企业贷款','50',450,  2850000.00, 2750000.00, 2850000.00, 1425000.00, NULL),
  ('2026-04-30','LN0003','C003','重庆丙公司','B002','江北支行','B002','H000','总行','O003','江北观音桥营业部','LP05','流动资金贷款','20', 45, 4950000.00, 4950000.00, 4950000.00, 247500.00, NULL),
  ('2026-04-30','LN0004','C004','重庆丁公司','B002','江北支行','B002','H000','总行',NULL,NULL,'LP07','消费贷款','30',150, 1100000.00, 1100000.00, 1100000.00, 330000.00, NULL),
  ('2026-04-30','LN0005','C005','重庆戊公司','B001','渝北支行',NULL,NULL,NULL,NULL,NULL,'LP01','个人住房贷款','10',NULL, 1960000.00, 1960000.00, 1960000.00, 9800.00, NULL),
  -- D-01：这一行的 accounting_org_no 与 org_no 不同（支行下辖记账）
  ('2026-04-30','LN0006','C006','重庆己公司','B002','江北支行','O003','H000','总行','O003','江北观音桥营业部','LP03','小微企业贷款','40',300,  1500000.00, 1400000.00, 1500000.00, 600000.00, NULL);

INSERT INTO qa_writeoff_detail VALUES
  (1,'LN0002','2026-04-15', 500000.00, 480000.00, 120000.00),
  (2,'LN0004','2026-04-20', 200000.00, 195000.00,  30000.00);

INSERT INTO rpt_npl_by_product_202604 VALUES
  ('202604','LP01','个人住房贷款',       0.00, 0.000000),
  ('202604','LP03','小微企业贷款', 2850000.00, 0.140700),
  ('202604','LP05','流动资金贷款', 4950000.00, 0.000000),
  ('202604','LP07','消费贷款',     1100000.00, 0.300000);

INSERT INTO rpt_npl_by_product_202605 VALUES
  ('202605','LP01','个人住房贷款',       0.00, 0.000000),
  ('202605','LP03','小微企业贷款', 2900000.00, 0.145000),
  ('202605','LP05','流动资金贷款', 4900000.00, 0.000000),
  ('202605','LP07','消费贷款',     1080000.00, 0.310000);

INSERT INTO rpt_npl_by_product_202606 VALUES
  ('202606','LP01','个人住房贷款',       0.00, 0.000000),
  ('202606','LP03','小微企业贷款', 2950000.00, 0.150000),
  ('202606','LP05','流动资金贷款', 4850000.00, 0.000000),
  ('202606','LP07','消费贷款',     1060000.00, 0.320000);

-- D-04：同名 npl_rate，口径是"逾期 90 天以上 / 总余额"，与上面四张报表不同
INSERT INTO rpt_npl_by_overdue_202604 VALUES
  ('202604','LP01','个人住房贷款',       0.00, 0.000000),
  ('202604','LP03','小微企业贷款', 2600000.00, 0.128300),
  ('202604','LP05','流动资金贷款', 4700000.00, 0.000000),
  ('202604','LP07','消费贷款',     1050000.00, 0.286400);


-- =============================================================================
--  刻意保留的缺陷清单
--
--  D-01  同义字段冗余：org_no 与 accounting_org_no 99% 相同，但不总是。
--        检验：用户问"渝北支行"时，按哪个字段过滤？两种答案都"看起来对"。
--  D-02  口径歧义：current_balnc_cny vs credit_loan_balnc；
--        writeoff_principal vs writeoff_balance。同名不同义。
--  D-03  缺注释：约 30% 的列没有 COMMENT。检验：没有语义描述时还能不能检索到。
--  D-04  混口径：rpt_npl_by_overdue_202604.npl_rate 与
--        rpt_npl_by_product_*.npl_rate 同名不同义。检验：域隔离是否真的生效。
--  D-05  历史垃圾：dm_loan_old_2023、tmp_loan_bak_0725。
--        检验：选表阶段能不能排除。
--  D-06  高基数名称：隶属网点（belong_to_org_*）真实库里有 300+ 个值。
--        检验：网点名必须等值匹配，不能 LIKE / 前缀 / 包含。
--  D-07  名称/编码双字段：prd_no + prd_nm。
--        检验：R-NAME-001（名称字段禁止 GROUP BY）。
--  D-08  自引用层级：dim_org.parent_org_no。检验：递归查询。
--  D-09  NULL 语义不明：overdue_day IS NULL 到底是"未逾期"还是"未采集"？
--        无注释可查，必须靠业务知识或 sample_value 推断。这是最阴的坑。
--  D-10  时点缺失：没有 2026-03-31 的快照（模拟 ETL 失败）。
--        检验：时间口径是否正确，"近3个月"到底是哪三个月。
--  D-11  同 schema 月分表：rpt_npl_by_product_2026{04,05,06}。
--        检验：表家族识别 + 多表 UNION ALL。
--
--  以下 Oracle 生产环境的差异点，Postgres 测不到，需要 profile: oracle：
--    * DATE '2026-04-30' 字面量格式（Oracle 需要 TO_DATE 或 ANSI 字面量）
--    * DUAL 伪表（标量子查询外层）
--    * 空字符串 = NULL
--    * TO_CHAR(date, 'YYYYMM') 月份派生
--    * 隐式类型转换导致的 ORA-01861（EXPLAIN 检查不出来，必须真空跑）
-- =============================================================================
