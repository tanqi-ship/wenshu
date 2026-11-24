"""真正独立进程start/reply/show；不使用内存状态或真实模型。"""

import json
import os
import subprocess
import sys

import pytest

from app.llm.fake import FakeLLM
from scripts import demo_session


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "scripts.demo_session", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "WENSHU_LLM_MODE": "real"},
        timeout=20,
    )


def payload(result):
    assert result.returncode == 0, result.stderr
    banner, text = result.stdout.split("\n", 1)
    assert "不执行业务SQL" in banner
    return json.loads(text)


def test_separate_process_recovery(tmp_path):
    db = tmp_path / "sessions.db"
    one = payload(cli("start", "--case", "SS-001", "--db", db))
    assert (one["status"], one["version"]) == ("awaiting_slot", 1)
    two = payload(
        cli(
            "reply",
            "--case",
            "SS-001",
            "--step",
            "1",
            "--db",
            db,
            "--session-id",
            one["session_id"],
            "--expected-version",
            "1",
        )
    )
    assert two["status"] == "slots_complete"
    assert two["intent"]["filters"][0]["value"] == "渝北"
    assert two["intent"]["time_range"]["points"] == ["2026-04-30"]
    shown = payload(cli("show", "--db", db, "--session-id", one["session_id"]))
    assert shown["completion"] is None
    assert shown["intent"] == two["intent"]
    stale = cli(
        "reply", "--case", "SS-001", "--db", db, "--session-id", one["session_id"], "--expected-version", "1"
    )
    assert stale.returncode == 1 and "SESSION_VERSION_CONFLICT" in stale.stderr


@pytest.mark.parametrize("case,status", [("SS-006", "budget_exhausted"), ("SS-007", "restart_required")])
def test_stopping_is_normal_protocol_result(tmp_path, case, status):
    db = tmp_path / "sessions.db"
    result = payload(cli("start", "--case", case, "--db", db))
    steps = 2 if case == "SS-006" else 1
    for step in range(1, steps + 1):
        result = payload(
            cli(
                "reply",
                "--case",
                case,
                "--step",
                step,
                "--db",
                db,
                "--session-id",
                result["session_id"],
                "--expected-version",
                result["version"],
            )
        )
    assert result["status"] == status and result["intent"] is None


def test_show_does_not_call_model(tmp_path, monkeypatch, capsys):
    db = tmp_path / "sessions.db"
    one = payload(cli("start", "--case", "SS-001", "--db", db))

    def forbidden(*args, **kwargs):
        pytest.fail("show不得创建回放或真实模型客户端")

    monkeypatch.setattr(demo_session, "ReplayLLM", forbidden)
    assert demo_session.main(["show", "--db", str(db), "--session-id", one["session_id"]]) == 0
    shown = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert shown["completion"] is None


def test_real_env_still_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WENSHU_LLM_MODE", "real")

    def forbidden(*args, **kwargs):
        pytest.fail("禁止真实模型")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.__init__", forbidden)
    assert demo_session.main(["start", "--case", "SS-001", "--db", str(tmp_path / "session.db")]) == 0
    assert '"status": "awaiting_slot"' in capsys.readouterr().out


def test_bad_response_cli_nonzero_without_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo_session, "ReplayLLM", lambda *a, **kw: FakeLLM(["坏响应"]))
    assert demo_session.main(["start", "--db", str(tmp_path / "session.db")]) == 1
    assert "EXTRACTION_JSON_INVALID" in capsys.readouterr().err


def test_missing_db_show_no_creation(tmp_path, capsys):
    db = tmp_path / "missing.db"
    assert (
        demo_session.main(["show", "--db", str(db), "--session-id", "00000000-0000-0000-0000-000000000001"])
        == 1
    )
    assert not db.exists()
    assert "SESSION_NOT_FOUND" in capsys.readouterr().err
