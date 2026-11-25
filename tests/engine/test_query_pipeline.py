import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from app.engine.clarify.session import ClarificationService, MemorySessionStore, SessionError
from app.engine.execution import ExecutionError, QueryRows
from app.engine.query import QueryRejection, QueryService, QueryValidationError
from app.engine.scope import Scope
from app.engine.semantic.bank_mini import TABLE, TABLE_COLUMNS, semantic_catalog
from app.engine.validation.catalog import Catalog
from app.llm.replay import ReplayLLM
from scripts.demo_query import AQ_001_CASSETTE, AQ_001_QUESTION
from scripts.seed_sqlite_business import seed_business

_CONTEXT = {"owner_key": "test", "catalog_revision": "test-v1"}


def service(executor, cassette=AQ_001_CASSETTE, *, physical=None):
    return QueryService(
        ClarificationService(store=MemorySessionStore(), llm=ReplayLLM(cassette, strict=True)),
        semantic_catalog(),
        physical if physical is not None else Catalog.from_mapping({"main": {TABLE: TABLE_COLUMNS}}),
        executor,
    )


async def test_clarification_skips_business_execution():
    executor = Mock()
    query = service(executor, Path("tests/cassettes/intent_extraction/EX-018.json"))
    result = await query.start("2026年4月末", **_CONTEXT, scope=Scope())
    assert result.status == "awaiting_slot"
    executor.execute.assert_not_called()


@pytest.mark.parametrize("kind", ["compile", "validate"])
async def test_failure_stops_before_execute(kind):
    executor = Mock()
    query = service(executor, physical=Catalog())
    scope = Scope() if kind == "compile" else Scope.of([TABLE])
    if kind == "compile":
        result = await query.start(AQ_001_QUESTION, **_CONTEXT, scope=scope)
        assert isinstance(result, QueryRejection)
        assert result.code == "OUT_OF_SCOPE"
    else:
        with pytest.raises(QueryValidationError):
            await query.start(AQ_001_QUESTION, **_CONTEXT, scope=scope)
    executor.execute.assert_not_called()


async def test_no_implicit_unrestricted_scope():
    executor = Mock()
    with pytest.raises(TypeError, match="Scope"):
        await service(executor).start(AQ_001_QUESTION, **_CONTEXT, scope=None)
    executor.execute.assert_not_called()


async def test_unknown_metric_is_unanswerable_before_validation_or_execution(monkeypatch):
    from app.engine import query as module

    examiner = Mock(wraps=module.validate_sql)
    monkeypatch.setattr(module, "validate_sql", examiner)
    executor = Mock()
    query = service(executor, Path("tests/cassettes/intent_extraction/EX-009.json"))
    result = await query.start("2026年4月末贷款迁徙率", **_CONTEXT, scope=Scope.of([TABLE]))
    assert result == QueryRejection("UNANSWERABLE", "当前不支持查询该指标。", result.session_id, 1)
    examiner.assert_not_called()
    executor.execute.assert_not_called()


@pytest.mark.parametrize(
    ("scope", "code"),
    [
        (Scope(), "METRIC_OUT_OF_SCOPE"),
        (Scope.of([TABLE], columns={TABLE: frozenset({"etl_dt"})}), "COLUMN_NOT_IN_SCOPE"),
    ],
)
async def test_scope_compile_errors_are_out_of_scope(scope, code, monkeypatch):
    from app.engine import query as module

    examiner = Mock(wraps=module.validate_sql)
    monkeypatch.setattr(module, "validate_sql", examiner)
    executor = Mock()
    result = await service(executor).start(AQ_001_QUESTION, **_CONTEXT, scope=scope)
    assert isinstance(result, QueryRejection)
    assert result.code == "OUT_OF_SCOPE"
    assert code not in result.message
    examiner.assert_not_called()
    executor.execute.assert_not_called()


async def test_only_checked_sql_passes_and_no_automatic_reexecution(monkeypatch):
    from app.engine import query as module

    examiner = Mock(wraps=module.validate_sql)
    monkeypatch.setattr(module, "validate_sql", examiner)
    executor = Mock()
    executor.execute.return_value = QueryRows(("loan_balance",), ((20_210_000,),), False)
    query = service(executor)
    result = await query.start(AQ_001_QUESTION, **_CONTEXT, scope=Scope.of([TABLE]))
    executor.execute.assert_called_once_with(examiner.call_args.args[0])
    assert result.result == executor.execute.return_value
    with pytest.raises(SessionError, match="CLOSED"):
        await query.reply(
            result.session.session_id, "贷款余额", **_CONTEXT, expected_version=1, scope=Scope()
        )
    executor.execute.assert_called_once()


async def test_driver_failure_not_retried():
    executor = Mock()
    executor.execute.side_effect = ExecutionError("EXECUTION_TIMEOUT", "测试超时")
    with pytest.raises(ExecutionError, match="EXECUTION_TIMEOUT"):
        await service(executor).start(AQ_001_QUESTION, **_CONTEXT, scope=Scope.of([TABLE]))
    executor.execute.assert_called_once()


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "scripts.demo_query", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WENSHU_LLM_MODE": "replay"},
        timeout=20,
    )


def payload(completed):
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_aq001_full_and_row_scoped(tmp_path):
    cases = yaml.safe_load(Path("eval/cases/asset_quality.yaml").read_text(encoding="utf-8"))
    expected = next(case for case in cases if case["id"] == "AQ-001")
    assert expected["question"] == AQ_001_QUESTION
    business = tmp_path / "business.db"
    seed_business(business)
    args = ("--business-db", business, "--session-db", tmp_path / "state.db")
    all_rows = payload(cli("start", *args))
    assert all_rows["session"]["rounds_used"] == 0
    assert all_rows["result"] == {
        "columns": ["loan_balance"],
        "rows": [[expected["expected"]["result"]]],
        "truncated": False,
    }
    assert payload(cli("start", *args, "--scope", "B001"))["result"]["rows"] == [[12_660_000]]
    denied = cli("start", *args, "--scope", "none")
    assert denied.returncode == 0 and json.loads(denied.stdout)["code"] == "OUT_OF_SCOPE"


def test_cli_returns_business_rejection_without_internal_details(tmp_path):
    business = tmp_path / "business.db"
    seed_business(business)
    result = cli(
        "start", "--case", "EX-009", "--business-db", business, "--session-db", tmp_path / "state.db"
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["code"] == "UNANSWERABLE"
    assert payload["message"] == "当前不支持查询该指标。"
    assert "qa_loan_acc_detail" not in result.stdout
    assert "UNKNOWN_METRIC" not in result.stdout


def test_cross_process_reply_uses_current_scope(tmp_path):
    business = tmp_path / "business.db"
    args = ("--business-db", business, "--session-db", tmp_path / "state.db")
    start = payload(cli("start", *args, "--case", "SS-002", "--scope", "B002"))
    assert start["status"] == "awaiting_slot"
    assert not business.exists()
    seed_business(business)
    reply = payload(
        cli("reply", *args, "--session-id", start["session_id"], "--expected-version", "1", "--scope", "B001")
    )
    assert reply["session"]["version"] == 2
    assert reply["result"]["rows"] == [[12_660_000]]


def test_cli_rejects_same_business_and_state_file(tmp_path):
    business = tmp_path / "business.db"
    seed_business(business)
    before = business.read_bytes()
    result = cli("start", "--business-db", business, "--session-db", business)
    assert result.returncode == 1 and "EXECUTION_INVALID_CONFIG" in result.stderr
    assert business.read_bytes() == before
