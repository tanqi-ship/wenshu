"""最小语义层 fixture：一个指标的完整定义长什么样。

★ **这是演示与测试用的假数据，不是生产配置。**

它存在的理由是：语义层要有**唯一一份**演示副本。在此之前它被手写了三份
（``scripts/demo_ask.py``、``tests/engine/test_compiler_minimal.py``、
``tests/conftest.py`` 的表结构），而黄金集是第四个消费者 —— 再加一份必然漂移，
而"语义层漂移"会让黄金集失败得莫名其妙（分不清是引擎错了还是配置变了）。

真正的语义层**从哪加载**（YAML / 应用库 / 注册中心）属于 M2/M3 的事。
到那时这份文件会被替换，而不是被扩展。

两个层次的数据刻意分开：

* :func:`semantic_catalog` —— 知识（指标公式、维度口径、码值），编译器读它
* :func:`validation_catalog` —— 名字（表在哪个 schema、有哪些列），校验器读它

两者在 ``docs/ARCHITECTURE.md`` 第二节和 ``validation/catalog.py`` 里都写了为什么分开：
语义层要被"查"，表结构只要被"比对存在性"。
"""

from __future__ import annotations

from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef, LevelDef
from app.engine.semantic.models import MetricDef
from app.engine.validation.catalog import Catalog

#: 主表。与 ``fixtures/sql/bank_fixture.sql`` 里的表同名（那份才是真实 schema）。
TABLE = "qa_loan_acc_detail"

#: 语义层里登记过的列。刻意**不**包含表里的全部列 ——
#: 语义层只登记"业务认识"的列，这正是它与表结构解耦的地方。
TABLE_COLUMNS: tuple[str, ...] = (
    "etl_dt",
    "org_no",
    "org_nm",
    "accounting_org_no",
    "prd_no",
    "prd_nm",
    "fiv_lev_cla_flag",
    "current_balnc_cny",
    "ecl_final",
)


def semantic_catalog() -> SemanticCatalog:
    """三个指标 + 两个维度，覆盖了 M1 支持的全部形状。"""
    return SemanticCatalog.build(
        metrics=[
            MetricDef(
                code="npl_balance",
                name="不良贷款余额",
                # 带占位符的写法：{tbl} 由编译器替换成本次表别名。
                formula=(
                    "SUM(CASE WHEN {tbl}.fiv_lev_cla_flag IN ('30','40','50') "
                    "THEN {tbl}.current_balnc_cny ELSE 0 END)"
                ),
                unit="元",
                source_tables=(TABLE,),
                synonyms=("不良余额", "不良贷款"),
            ),
            MetricDef(
                code="loan_balance",
                name="贷款余额",
                # ★ 刻意**不带**占位符：这是历史数据的常见写法，
                #   用来验证 qualify_columns 会把未限定的列补上表别名。
                formula="SUM(current_balnc_cny)",
                unit="元",
                source_tables=(TABLE,),
                synonyms=("各项贷款余额",),
            ),
            MetricDef(
                code="npl_ratio",
                name="不良贷款率",
                formula=(
                    "SUM(CASE WHEN {tbl}.fiv_lev_cla_flag IN ('30','40','50') "
                    "THEN {tbl}.current_balnc_cny ELSE 0 END)"
                    " / NULLIF(SUM({tbl}.current_balnc_cny), 0)"
                ),
                unit="%",
                source_tables=(TABLE,),
            ),
        ],
        dimensions=[
            DimensionDef(
                code="org",
                name="机构",
                # ★ 两个口径对应两组物理列。选哪个由 level 决定，
                #   不再靠 prompt 里一句"网点默认管户机构"去让模型记。
                levels={
                    "financial": LevelDef(group_by="org_no", display="org_nm"),
                    "customer_management": LevelDef(group_by="accounting_org_no", display="org_nm"),
                },
                default_level="financial",
                synonyms=("分行", "支行", "网点"),
            ),
            DimensionDef(
                code="product",
                name="产品",
                levels={"default": LevelDef(group_by="prd_no", display="prd_nm")},
                default_level="default",
                synonyms=("贷款产品", "产品类型"),
            ),
        ],
        code_map={
            # 维度值 → 数据库存储值。**没有配置的维度，值会原样透传**。
            # 这正是 fixture 缺陷 D-06 的坑：机构名是高基数值，
            # 必须精确等值，不能 LIKE / 前缀 / 包含（ADR-0002）。
            "org": {
                "渝北支行": "B001",
                "两江分行": "O003",
            },
            "product": {
                "个人住房贷款": "LP01",
                "小微企业贷款": "LP03",
                "流动资金贷款": "LP05",
                "消费贷款": "LP07",
            },
        },
    )


def validation_catalog() -> Catalog:
    """校验器用的表结构目录（只有名字，没有业务含义）。

    刻意让 schema 是 ``public``：校验器保留 schema 这一层，
    同名表出现在不同 schema 时要求写全限定名，而不是靠 search_path 猜。
    """
    return Catalog.from_mapping({"public": {TABLE: list(TABLE_COLUMNS)}})
