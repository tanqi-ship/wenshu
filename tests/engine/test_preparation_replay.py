"""固定回放检验M3.2的独立手写预期与旧L3/L4边界。"""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import yaml

from app.engine.clarify import prepare_question
from app.engine.compiler import CompileError, compile_sql
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import intent_from_mapping
from app.engine.validation import validate_sql
from app.llm.fake import FakeLLM
from app.llm.protocol import ReplayMiss
from app.llm.replay import ReplayLLM

CASES = yaml.safe_load(Path("tests/fixtures/clarification/cases.yaml").read_text(encoding="utf-8"))["cases"]
EXTRACTION_CASES = {
    c["id"]: c
    for c in yaml.safe_load(Path("tests/fixtures/intent_extraction/cases.yaml").read_text(encoding="utf-8"))[
        "cases"
    ]
}


def test_manifest_covers_old_fixed_cases():
    assert len(CASES) == len({c["id"] for c in CASES}) == 20
    assert {c["extraction_case"] for c in CASES} == set(EXTRACTION_CASES)
    assert all(c["provenance"] == "handwritten_fixture" and c["why_hard"] for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_strict_replay_against_independent_preparation_expected(case, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("只允许纯离线回放")

    monkeypatch.setattr("app.llm.real.OpenAICompatLLM.__init__", forbidden)
    original = EXTRACTION_CASES[case["extraction_case"]]
    llm = ReplayLLM(original["cassette"], inner=None, strict=True)
    result = await prepare_question(original["question"], semantic_catalog(), llm=llm)
    assert result.status == case["expected_status"]
    assert result.missing_slots == tuple(case["expected_missing_slots"])
    assert (result.extraction is not None) == case["expected_has_extraction"]
    clarification = asdict(result.clarification) if result.clarification else None
    if clarification:
        clarification["fragments"] = list(clarification["fragments"])
    assert clarification == case["expected_clarification"]
    if result.extraction:
        intent = result.extraction.intent
        assert replace(intent, raw={}) == intent_from_mapping(original["expected"])
        completion = result.extraction.completion
        assert completion.model == "synthetic/handwritten-fixture"
        assert completion.total_tokens == 0
        assert completion.meta == {"replayed": True}
        if original.get("compile"):
            assert result.status == "slots_complete"
            sql = compile_sql(intent, semantic_catalog())
            assert sql == compile_sql(intent_from_mapping(original["expected"]), semantic_catalog())
            checked = validate_sql(sql, catalog=validation_catalog(), intent=intent)
            assert checked.ok, checked.codes
        if original.get("compile_error"):
            assert result.status == "slots_complete"
            with pytest.raises(CompileError) as exc:
                compile_sql(intent, semantic_catalog())
            assert exc.value.code == original["compile_error"]
    assert (llm.hits, llm.misses) == (1, 0)


async def test_replay_miss_visible_without_fallback():
    case = EXTRACTION_CASES["EX-001"]
    llm = ReplayLLM(case["cassette"], inner=None, strict=True)
    with pytest.raises(ReplayMiss):
        await prepare_question(case["question"] + "变更", semantic_catalog(), llm=llm)
    assert (llm.hits, llm.misses) == (0, 1)


@pytest.mark.parametrize("comparison", ["yoy", "mom", "rate"])
async def test_comparison_slots_complete_still_compiler_rejects(comparison):
    llm = FakeLLM(
        [
            json.dumps(
                {
                    "metrics": ["贷款余额"],
                    "time": {"grain": "month_end", "points": ["2026-04-30", "2026-05-31"]},
                    "comparison": comparison,
                    "unresolved": [],
                }
            )
        ]
    )
    result = await prepare_question("对比", semantic_catalog(), llm=llm)
    assert result.status == "slots_complete"
    intent = result.extraction.intent
    assert intent.comparison == comparison
    with pytest.raises(CompileError) as exc:
        compile_sql(intent, semantic_catalog())
    assert exc.value.code == "COMPARISON_UNSUPPORTED"
    assert llm.call_count == 1


async def test_global_topn_not_converted_to_monthly():
    case = EXTRACTION_CASES["EX-019"]
    result = await prepare_question(
        case["question"], semantic_catalog(), llm=ReplayLLM(case["cassette"], inner=None, strict=True)
    )
    assert result.status == "slots_complete"
    assert result.extraction.intent.top_n_grain is None
    with pytest.raises(CompileError) as exc:
        compile_sql(result.extraction.intent, semantic_catalog())
    assert exc.value.code == "TOPN_UNSUPPORTED"


@pytest.mark.parametrize("case_id", ["EX-001", "EX-002"])
async def test_external_scope_and_business_filter_both_survive(case_id):
    case = EXTRACTION_CASES[case_id]
    result = await prepare_question(
        case["question"], semantic_catalog(), llm=ReplayLLM(case["cassette"], inner=None, strict=True)
    )
    scope = Scope.of([TABLE], row_policies=[RowPolicy(TABLE, "org_no", ("B002",))])
    assert result.status == "slots_complete"
    intent = result.extraction.intent
    sql = compile_sql(intent, semantic_catalog(), scope=scope)
    assert "B002" in sql
    assert intent.org is None
    if case_id == "EX-002":
        assert "B001" in sql
        assert intent.filters[0].value == "渝北"
    checked = validate_sql(sql, catalog=validation_catalog(), intent=intent, scope=scope)
    assert checked.ok, checked.codes


async def test_slots_complete_does_not_authorize_empty_scope():
    case = EXTRACTION_CASES["EX-001"]
    result = await prepare_question(
        case["question"], semantic_catalog(), llm=ReplayLLM(case["cassette"], inner=None, strict=True)
    )
    assert result.status == "slots_complete"
    with pytest.raises(CompileError) as exc:
        compile_sql(result.extraction.intent, semantic_catalog(), scope=Scope())
    assert (exc.value.stage, exc.value.code) == ("scope", "METRIC_OUT_OF_SCOPE")
