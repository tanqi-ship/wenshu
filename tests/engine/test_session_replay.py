"""固定离线会话清单，预期手写；每步重建SQLite对象模拟跨请求。"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from app.engine.clarify.session import ClarificationService, SessionError, SQLiteSessionStore
from app.engine.compiler import CompileError, compile_sql
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import intent_from_mapping
from app.engine.validation import validate_sql
from app.llm.replay import ReplayLLM

CASES = yaml.safe_load(Path("tests/fixtures/clarification_session/cases.yaml").read_text(encoding="utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_independent_fixed_replay(case, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("只允许离线固定回放")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.__init__", forbidden)
    assert case["provenance"] == "handwritten_fixture" and case["why_hard"]
    path = tmp_path / "sessions.db"
    llm = ReplayLLM(case["start_cassette"], inner=None, strict=True)
    s = ClarificationService(store=SQLiteSessionStore(path), llm=llm)
    result = await s.start(case["question"], semantic_catalog(), owner_key="alice", catalog_revision="v1")
    assert result.status == case["expected_start"]
    assert (llm.hits, llm.misses) == (1, 0)
    for step in case["replies"]:
        llm = ReplayLLM(step["cassette"], inner=None, strict=True)
        s = ClarificationService(store=SQLiteSessionStore(path), llm=llm)
        result = await s.reply(
            result.session_id,
            step["answer"],
            semantic_catalog(),
            owner_key="alice",
            catalog_revision="v1",
            expected_version=result.version,
        )
        assert result.status == step["expected_status"]
        assert result.rounds_used == step["expected_rounds"]
        assert (llm.hits, llm.misses) == (1, 0)
        if result.completion:
            assert result.completion.model == "synthetic/handwritten-fixture"
            assert result.completion.total_tokens == 0
    assert result.status == case["expected_final"]
    assert result.version == 1 + len(case["replies"])
    shown = s.show(result.session_id, owner_key="alice")
    assert shown.completion is None
    assert shown.intent == result.intent
    if "expected_intent" in case:
        expected = intent_from_mapping(case["expected_intent"])
        assert replace(result.intent, raw={}) == expected
    else:
        assert result.intent is None
    if case.get("compile"):
        scope = Scope.of([TABLE], row_policies=[RowPolicy(TABLE, "org_no", ("B002",))])
        sql = compile_sql(result.intent, semantic_catalog(), scope=scope)
        assert sql == compile_sql(expected, semantic_catalog(), scope=scope)
        assert "B002" in sql
        checked = validate_sql(sql, catalog=validation_catalog(), intent=result.intent, scope=scope)
        assert checked.ok, checked.codes
        with pytest.raises(CompileError, match="METRIC_OUT_OF_SCOPE"):
            compile_sql(result.intent, semantic_catalog(), scope=Scope())
    if case.get("compile_error"):
        with pytest.raises(CompileError) as exc:
            compile_sql(result.intent, semantic_catalog())
        assert exc.value.code == case["compile_error"]
    with pytest.raises(SessionError, match="SESSION_CLOSED"):
        await s.reply(
            result.session_id,
            "2026年4月末",
            semantic_catalog(),
            owner_key="alice",
            catalog_revision="v1",
            expected_version=result.version,
        )
