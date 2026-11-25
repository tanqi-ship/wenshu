import sqlite3

import pytest

from app.engine import execution
from app.engine.execution import ExecutionError, QueryRows, SQLiteExecutor
from scripts.seed_sqlite_business import seed_business


@pytest.fixture
def business(tmp_path):
    path = tmp_path / "业务 #?.db".replace("?", "%")
    seed_business(path)
    return path


@pytest.mark.parametrize(("limit", "truncated"), [(1, True), (2, False), (1000, False)])
def test_row_limit(business, limit, truncated):
    rows = SQLiteExecutor(business, max_rows=limit).execute(
        "SELECT DISTINCT org_no FROM qa_loan_acc_detail ORDER BY org_no"
    )
    assert rows == QueryRows(("org_no",), (("B001",), ("B002",))[:limit], truncated)


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.0])
def test_invalid_row_limits(business, limit):
    with pytest.raises(ExecutionError, match="EXECUTION_INVALID_CONFIG"):
        SQLiteExecutor(business, max_rows=limit)


@pytest.mark.parametrize("timeout", [0, -1, False, float("inf"), float("nan")])
def test_invalid_timeouts(business, timeout):
    with pytest.raises(ExecutionError, match="EXECUTION_INVALID_CONFIG"):
        SQLiteExecutor(business, timeout_s=timeout)


def test_empty_and_null(business):
    db = SQLiteExecutor(business)
    assert db.execute("SELECT org_no FROM qa_loan_acc_detail WHERE 0") == QueryRows(("org_no",), (), False)
    assert db.execute(
        "SELECT SUM(current_balnc_cny) AS balance FROM qa_loan_acc_detail WHERE 0"
    ) == QueryRows(("balance",), ((None,),), False)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM qa_loan_acc_detail",
        "CREATE TEMP TABLE scratch (id INTEGER)",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE ':memory:' AS scratch",
        "SELECT load_extension('not-an-extension')",
    ],
)
def test_defense_in_depth_denies_operations(business, sql):
    before = business.read_bytes()
    with pytest.raises(ExecutionError, match="EXECUTION_DENIED"):
        SQLiteExecutor(business).execute(sql)
    assert business.read_bytes() == before


def test_missing_db_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(ExecutionError, match="EXECUTION_DB_UNAVAILABLE") as err:
        SQLiteExecutor(path).execute("SELECT 1")
    assert str(path) not in str(err.value)
    assert not path.exists()


def test_bad_db_and_driver_failure(business, tmp_path):
    bad = tmp_path / "invalid.db"
    bad.write_text("not a database", encoding="utf-8")
    with pytest.raises(ExecutionError, match="EXECUTION_DB_UNAVAILABLE"):
        SQLiteExecutor(bad).execute("SELECT * FROM qa_loan_acc_detail")
    with pytest.raises(ExecutionError, match="EXECUTION_FAILED"):
        SQLiteExecutor(business).execute("SELECT * FROM unknown_table")


def test_progress_interrupt_releases_real_connection(business, monkeypatch):
    connections = []
    connect = sqlite3.connect

    def capture(*args, **kwargs):
        db = connect(*args, **kwargs)
        connections.append(db)
        return db

    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(execution, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(execution.sqlite3, "connect", capture)
    with pytest.raises(ExecutionError, match="EXECUTION_TIMEOUT"):
        SQLiteExecutor(business).execute(
            "SELECT SUM(a.current_balnc_cny) FROM qa_loan_acc_detail a, qa_loan_acc_detail b, qa_loan_acc_detail c"
        )
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_seed_never_overwrites_existing_file(business):
    before = business.read_bytes()
    with pytest.raises(FileExistsError):
        seed_business(business)
    assert business.read_bytes() == before
