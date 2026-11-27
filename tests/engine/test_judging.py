"""共享判定器的单元测试与编译+SQLite 自检。

不调用模型、不联网。自检只覆盖语义层能编译、SQLite fixture 有表的用例：
AQ-001～005、008；AQ-006/AQ-007 用本文件手工构造的 QueryRows 覆盖匹配器。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from app.engine.clarify.session.models import SessionResult
from app.engine.compiler import compile_sql
from app.engine.execution import QueryRows, SQLiteExecutor
from app.engine.query import QueryAnswer, QueryRejection
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.semantic.models import intent_from_mapping
from app.engine.understanding import ExtractionError
from scripts.judging import judge_execution, judge_extraction
from scripts.seed_sqlite_business import seed_business

_EVAL_CASES = Path(__file__).resolve().parents[2] / "eval/cases/asset_quality.yaml"


def _session(*, note: str = "", status: str = "slots_complete") -> SessionResult:
    return SessionResult(
        session_id="test-session",
        version=1,
        status=status,  # type: ignore[arg-type]
        rounds_used=0,
        max_rounds=2,
        clarification=None,
        note=note,
        intent=None,
        completion=None,
    )


def _answer(rows: QueryRows, *, note: str = "") -> QueryAnswer:
    return QueryAnswer(session=_session(note=note), result=rows)


def _cases() -> dict[str, dict]:
    payload = yaml.safe_load(_EVAL_CASES.read_text(encoding="utf-8"))
    return {case["id"]: case for case in payload}


# ---------------------------------------------------------------------------
# 抽取判定
# ---------------------------------------------------------------------------


def test_extraction_success() -> None:
    case = {
        "expected": {
            "metric": "贷款余额",
            "time": {"grain": "month_end", "points": ["2026-04-30"]},
        },
        "compile": True,
    }
    verdict = judge_extraction(case, intent=intent_from_mapping(case["expected"]))
    assert verdict.ok


def test_extraction_ignores_raw_display_fields() -> None:
    case = {
        "expected": {
            "metric": "贷款余额",
            "time": {"grain": "month_end", "points": ["2026-04-30"]},
            "filters": [{"dimension": "机构", "value": "渝北"}],
        },
        "compile": True,
    }
    expected = intent_from_mapping(case["expected"])
    intent = replace(
        expected,
        time_range=replace(expected.time_range, raw="2026年4月末"),
        filters=tuple(replace(item, raw="渝北") for item in expected.filters),
        raw={"model": "ok"},
    )
    assert judge_extraction(case, intent=intent).ok


def test_extraction_expected_error_matches() -> None:
    case = {
        "expected_error": "EXTRACTION_UNRESOLVED",
        "expected_fragments": ["去年4月末"],
    }
    error = ExtractionError("EXTRACTION_UNRESOLVED", "无法解析相对时间", fragments=("去年4月末",))
    verdict = judge_extraction(case, error=error)
    assert verdict.ok
    assert verdict.details["expected"] == "EXTRACTION_UNRESOLVED"


def test_extraction_wrong_error_code_fails() -> None:
    case = {
        "expected_error": "EXTRACTION_UNRESOLVED",
        "expected_fragments": ["去年4月末"],
    }
    error = ExtractionError("EXTRACTION_MULTI_METRIC_UNSUPPORTED", "多指标")
    verdict = judge_extraction(case, error=error)
    assert not verdict.ok
    assert verdict.stage == "understanding"
    assert verdict.code == "EXTRACTION_MULTI_METRIC_UNSUPPORTED"


def test_extraction_expected_compile_error_passes() -> None:
    case = {
        "expected": {
            "metric": "贷款迁徙率",
            "time": {"grain": "month_end", "points": ["2026-04-30"]},
        },
        "compile_error": "UNKNOWN_METRIC",
    }
    verdict = judge_extraction(case, intent=intent_from_mapping(case["expected"]))
    assert verdict.ok
    assert verdict.details["expected"] == "UNKNOWN_METRIC"


def test_extraction_unexpected_success_fails() -> None:
    case = {"expected_error": "EXTRACTION_UNRESOLVED"}
    intent = intent_from_mapping(
        {"metric": "贷款余额", "time": {"grain": "month_end", "points": ["2026-04-30"]}}
    )
    verdict = judge_extraction(case, intent=intent)
    assert not verdict.ok
    assert verdict.stage == "understanding"
    assert verdict.code == "UNEXPECTED_SUCCESS"


def test_extraction_intent_mismatch_fails() -> None:
    case = {
        "expected": {
            "metric": "贷款余额",
            "time": {"grain": "month_end", "points": ["2026-04-30"]},
        }
    }
    intent = intent_from_mapping(
        {"metric": "不良贷款余额", "time": {"grain": "month_end", "points": ["2026-04-30"]}}
    )
    verdict = judge_extraction(case, intent=intent)
    assert not verdict.ok
    assert verdict.code == "INTENT_MISMATCH"


# ---------------------------------------------------------------------------
# 执行判定：四种结果形状
# ---------------------------------------------------------------------------


def test_execution_scalar_matches() -> None:
    rows = QueryRows(columns=("loan_balance",), rows=((20210000.0,),), truncated=False)
    verdict = judge_execution({"expected": {"result": 20210000.0}}, _answer(rows))
    assert verdict.ok


@pytest.mark.parametrize(
    ("actual", "ok"),
    [(20210000.0000005, True), (20210000.000002, False)],
)
def test_execution_scalar_tolerance(actual: float, ok: bool) -> None:
    rows = QueryRows(columns=("loan_balance",), rows=((actual,),), truncated=False)
    verdict = judge_execution({"expected": {"result": 20210000.0}}, _answer(rows))
    assert verdict.ok is ok
    if not ok:
        assert verdict.stage == "data"
        assert verdict.code == "RESULT_MISMATCH"


def test_execution_row_key_mapping() -> None:
    rows = QueryRows(
        columns=("prd_no", "prd_nm", "loan_balance"),
        rows=(
            ("LP01", "个人住房贷款", 9810000.0),
            ("LP03", "小微企业贷款", 4350000.0),
            ("LP05", "流动资金贷款", 4950000.0),
            ("LP07", "消费贷款", 1100000.0),
        ),
        truncated=False,
    )
    expected = {
        "result": {
            "LP01": 9810000.00,
            "LP03": 4350000.00,
            "LP05": 4950000.00,
            "LP07": 1100000.00,
        }
    }
    assert judge_execution({"expected": expected}, _answer(rows)).ok

    wrong = QueryRows(columns=rows.columns, rows=rows.rows[:3], truncated=False)
    verdict = judge_execution({"expected": expected}, _answer(wrong))
    assert not verdict.ok
    assert verdict.code == "RESULT_MISMATCH"


def test_execution_column_name_mapping() -> None:
    rows = QueryRows(
        columns=("writeoff_principal", "recovered_amount"),
        rows=((700000.0, 150000.0),),
        truncated=False,
    )
    expected = {"result": {"writeoff_principal": 700000.00, "recovered_amount": 150000.00}}
    assert judge_execution({"expected": expected}, _answer(rows)).ok


def test_execution_multi_row_mapping() -> None:
    rows = QueryRows(
        columns=("etl_dt", "org_no", "org_nm", "loan_balance"),
        rows=(
            ("2026-01-31", "B001", "渝北支行", 13000000.0),
            ("2026-02-28", "B001", "渝北支行", 12880000.0),
            ("2026-04-30", "B001", "渝北支行", 12660000.0),
        ),
        truncated=False,
    )
    expected = {
        "result": [
            {"etl_dt": "2026-01-31", "org_no": "B001", "loan_balance": 13000000.00},
            {"etl_dt": "2026-02-28", "org_no": "B001", "loan_balance": 12880000.00},
            {"etl_dt": "2026-04-30", "org_no": "B001", "loan_balance": 12660000.00},
        ]
    }
    assert judge_execution({"expected": expected}, _answer(rows)).ok

    reordered = QueryRows(columns=rows.columns, rows=rows.rows[::-1], truncated=False)
    verdict = judge_execution({"expected": expected}, _answer(reordered))
    assert not verdict.ok
    assert verdict.code == "RESULT_MISMATCH"


def test_execution_rejection_is_scope_failure_for_result_case() -> None:
    rejection = QueryRejection("OUT_OF_SCOPE", "当前权限无法完成该查询。", "test-session", 1)
    verdict = judge_execution({"expected": {"result": 20210000.0}}, rejection)
    assert not verdict.ok
    assert verdict.stage == "scope"
    assert verdict.code == "OUT_OF_SCOPE"


def test_execution_extraction_error_is_understanding() -> None:
    error = ExtractionError("EXTRACTION_SCHEMA_INVALID", "协议错误", path="unresolved[0]")
    verdict = judge_execution({"expected": {"result": 20210000.0}}, error=error)
    assert not verdict.ok
    assert verdict.stage == "understanding"
    assert verdict.code == "EXTRACTION_SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# 执行判定：两条行为题
# ---------------------------------------------------------------------------


def test_data_missing_clarification_passes() -> None:
    session = _session(status="awaiting_slot")
    verdict = judge_execution({"expected": {"behavior": "clarify_or_disclose"}}, session)
    assert verdict.ok


def test_data_missing_disclosed_answer_passes() -> None:
    rows = QueryRows(columns=("loan_balance",), rows=((None,),), truncated=False)
    verdict = judge_execution(
        {"expected": {"behavior": "clarify_or_disclose"}},
        _answer(rows, note="2026-03-31 快照缺失；最近可用为 2026-02-28。"),
    )
    assert verdict.ok


def test_data_missing_silent_answer_fails() -> None:
    rows = QueryRows(columns=("loan_balance",), rows=((0.0,),), truncated=False)
    verdict = judge_execution({"expected": {"behavior": "clarify_or_disclose"}}, _answer(rows))
    assert not verdict.ok
    assert verdict.stage == "data"
    assert verdict.code == "SILENT_ANSWER"


def test_unanswerable_rejection_passes() -> None:
    rejection = QueryRejection("UNANSWERABLE", "当前不支持查询该指标。", "test-session", 1)
    verdict = judge_execution({"expected": {"behavior": "refuse_with_alternative"}}, rejection)
    assert verdict.ok
    assert verdict.details["alternative"] == "当前不支持查询该指标。"


def test_unanswerable_answer_fails() -> None:
    rows = QueryRows(columns=("loan_balance",), rows=((42.0,),), truncated=False)
    verdict = judge_execution({"expected": {"behavior": "refuse_with_alternative"}}, _answer(rows))
    assert not verdict.ok
    assert verdict.code == "ANSWERED_UNANSWERABLE"


# ---------------------------------------------------------------------------
# AQ-006 / AQ-007：没有 SQLite fixture 或语义层支持，用手工结果覆盖匹配器
# ---------------------------------------------------------------------------


def test_row_key_matcher_covers_report_case() -> None:
    expected = _cases()["AQ-006"]["expected"]
    rows = QueryRows(
        columns=("prd_no", "prd_nm", "npl_rate"),
        rows=(
            ("LP01", "个人住房贷款", 0.0),
            ("LP03", "小微企业贷款", 0.1407),
            ("LP05", "流动资金贷款", 0.0),
            ("LP07", "消费贷款", 0.3),
        ),
        truncated=False,
    )
    assert judge_execution({"expected": expected}, _answer(rows)).ok


def test_column_name_matcher_covers_writeoff_case() -> None:
    expected = _cases()["AQ-007"]["expected"]
    rows = QueryRows(
        columns=("writeoff_principal", "recovered_amount"),
        rows=((700000.0, 150000.0),),
        truncated=False,
    )
    assert judge_execution({"expected": expected}, _answer(rows)).ok


# ---------------------------------------------------------------------------
# 自检：正确 Intent 编译出的真实 SQLite 结果必须全部判对
# ---------------------------------------------------------------------------

_SELF_CHECK_INTENTS = {
    "AQ-001": {
        "metric": "贷款余额",
        "time": {"grain": "month_end", "points": ["2026-04-30"]},
    },
    "AQ-002": {
        "metric": "不良贷款余额",
        "time": {"grain": "month_end", "points": ["2026-04-30"]},
        "filters": [{"dimension": "机构", "value": "渝北", "raw": "渝北"}],
    },
    "AQ-003": {
        "metric": "不良贷款率",
        "time": {"grain": "month_end", "points": ["2026-04-30"]},
    },
    "AQ-004": {
        "metric": "贷款余额",
        "time": {"grain": "month_end", "points": ["2026-04-30", "2026-01-31"]},
        "comparison": "delta",
    },
    "AQ-005": {
        "metric": "贷款余额",
        "time": {"grain": "month_end", "points": ["2026-04-30"]},
        "dimensions": ["产品"],
    },
    "AQ-008": {
        "metric": "贷款余额",
        "time": {"grain": "month", "start": "2026-01-01", "end": "2026-04-30"},
        "dimensions": ["机构"],
        "top_n": 1,
        "top_n_grain": "month",
    },
}


@pytest.mark.parametrize("case_id", sorted(_SELF_CHECK_INTENTS))
def test_self_check_compiled_result_is_judged_ok(case_id: str) -> None:
    expected = _cases()[case_id]["expected"]
    intent = intent_from_mapping(_SELF_CHECK_INTENTS[case_id])

    with TemporaryDirectory(prefix="wenshu-judge-") as directory:
        database = Path(directory) / "business.db"
        seed_business(database)
        rows = SQLiteExecutor(database).execute(compile_sql(intent, semantic_catalog(), dialect="sqlite"))

    verdict = judge_execution({"expected": expected}, _answer(rows))
    assert verdict.ok, json.dumps(verdict.details, ensure_ascii=False, default=str)
