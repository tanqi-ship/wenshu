"""严格固定回放 → 独立手写 Intent → 既有 L3/L4，不更改黄金 SQL。"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from app.engine.compiler import CompileError, compile_sql
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import intent_from_mapping
from app.engine.understanding import ExtractionError, extract_intent
from app.engine.validation import validate_sql
from app.llm.protocol import ReplayMiss
from app.llm.replay import ReplayLLM

CASES = yaml.safe_load(Path("tests/fixtures/intent_extraction/cases.yaml").read_text(encoding="utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_fixed_replay(case, monkeypatch):
    # 真实 complete 若被错误触达立即失败，纯回放也无 inner。
    async def forbidden(*args, **kwargs):
        pytest.fail("离线回归禁止真实模型调用")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.complete", forbidden)
    assert case["provenance"] == "handwritten_fixture"
    assert case["why_hard"]
    llm = ReplayLLM(case["cassette"], inner=None, strict=True)
    if "expected_error" in case:
        with pytest.raises(ExtractionError) as exc:
            await extract_intent(case["question"], semantic_catalog(), llm=llm)
        assert exc.value.code == case["expected_error"]
        assert exc.value.fragments == tuple(case.get("expected_fragments", []))
    else:
        result = await extract_intent(case["question"], semantic_catalog(), llm=llm)
        expected = intent_from_mapping(case["expected"])
        assert replace(result.intent, raw={}) == expected
        assert result.completion.model == "synthetic/handwritten-fixture"
        assert result.completion.total_tokens == 0
        assert result.completion.meta == {"replayed": True}
        if case.get("compile"):
            sql = compile_sql(result.intent, semantic_catalog())
            assert sql == compile_sql(expected, semantic_catalog())
            checked = validate_sql(sql, catalog=validation_catalog(), intent=result.intent)
            assert checked.ok, checked.codes
        if case.get("compile_error"):
            with pytest.raises(CompileError) as exc:
                compile_sql(result.intent, semantic_catalog())
            assert exc.value.code == case["compile_error"]
    assert (llm.hits, llm.misses) == (1, 0)


async def test_prompt_drift_visible():
    case = CASES[0]
    llm = ReplayLLM(case["cassette"], inner=None, strict=True)
    with pytest.raises(ReplayMiss):
        await extract_intent(case["question"] + " 改动", semantic_catalog(), llm=llm)
    assert (llm.hits, llm.misses) == (0, 1)


async def test_scope_stays_external_and_enforced():
    case = CASES[0]  # 全行不会取消外部权限。
    result = await extract_intent(
        case["question"], semantic_catalog(), llm=ReplayLLM(case["cassette"], inner=None, strict=True)
    )
    scope = Scope.of([TABLE], row_policies=[RowPolicy(TABLE, "org_no", ("B001",))])
    sql = compile_sql(result.intent, semantic_catalog(), scope=scope)
    assert "B001" in sql
    assert result.intent.org is None
    assert not result.intent.filters
    checked = validate_sql(sql, catalog=validation_catalog(), intent=result.intent, scope=scope)
    assert checked.ok, checked.codes
