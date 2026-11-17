"""共享测试夹具。

刻意让所有夹具都是**纯内存**的：不需要数据库、不需要网络、不需要模型。
这是为了守住一条纪律 —— 单元测试必须能在任何机器上、任何时候、秒级跑完。
需要真实数据库的测试单独打 ``@pytest.mark.db``。
"""

from __future__ import annotations

import pytest

from app.engine.semantic.models import Intent, TimeRange
from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import Scope, ValidationPolicy
from tests.constants import DIALECT

__all__ = ["DIALECT", "catalog", "intent_loan_balance", "policy", "scope"]


@pytest.fixture
def catalog() -> Catalog:
    """一个最小的假银行库结构。

    刻意保留了真实库里常见的坑：

    * ``org_no`` 与 ``accounting_org_no`` 同时存在（同义字段冗余）
    * ``prd_nm`` 是名称字段（触发 R-NAME-001）
    * ``etl_dt`` 是月末快照列（触发 R-COMP-001）
    * 表名带 ``qa_`` 前缀，模仿回归 fixture 的命名
    """
    return Catalog.from_mapping(
        {
            "public": {
                "qa_loan_acc_detail": [
                    "etl_dt",
                    "borrow_note_no",
                    "org_no",
                    "org_nm",
                    "accounting_org_no",
                    "prd_no",
                    "prd_nm",
                    "fiv_lev_cla_flag",
                    "overdue_day",
                    "current_balnc_cny",
                    "ecl_final",
                ],
                "qa_writeoff_detail": [
                    "writeoff_id",
                    "etl_dt",
                    "source_account_no",
                    "writeoff_principal",
                    "recovered_amount",
                ],
                "qa_credit_card_detail": [
                    "etl_dt",
                    "card_account_no",
                    "current_balance",
                    "nonperforming_balance",
                ],
                "rpt_npl_by_product_202604": ["stat_month", "prd_no", "npl_rate"],
                "rpt_npl_by_product_202605": ["stat_month", "prd_no", "npl_rate"],
            }
        }
    )


@pytest.fixture
def policy() -> ValidationPolicy:
    return ValidationPolicy()


@pytest.fixture
def scope() -> Scope:
    return Scope.of(["qa_loan_acc_detail", "qa_writeoff_detail"])


@pytest.fixture
def intent_loan_balance() -> Intent:
    return Intent(
        metric="loan_balance",
        time_range=TimeRange(grain="month_end", raw="2026年4月末"),
    )
