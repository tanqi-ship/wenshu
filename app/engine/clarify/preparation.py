"""无状态单轮准备：必需槽位齐全不等于可答、可编译或授权。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engine.clarify.slots import SlotSpec, check_slots
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.understanding import ExtractionError, ExtractionResult, extract_intent
from app.llm.protocol import LLMClient


@dataclass(frozen=True, slots=True)
class Clarification:
    reason: Literal["missing_slot", "unresolved", "multi_metric"]
    question: str
    slot: Literal["metric", "time_range"] | None = None
    fragments: tuple[str, ...] = ()
    source_error_code: str | None = None
    source_error_path: str | None = None


@dataclass(frozen=True, slots=True)
class PreparationResult:
    """extraction=None 时未运行槽位检查，missing_slots=() 不是齐全证明。"""

    status: Literal["needs_clarification", "slots_complete"]
    extraction: ExtractionResult | None
    missing_slots: tuple[str, ...]
    clarification: Clarification | None


def _required_slots(catalog: SemanticCatalog) -> tuple[SlotSpec, ...]:
    # 同次 extract_intent 已先验证目录。只用已登记业务名举例，不承诺权限或可答。
    examples = tuple(catalog.metrics[key].name for key in sorted(catalog.metrics)[:3])
    return (
        SlotSpec(
            name="metric",
            required=True,
            on_missing="ask",
            question="您想查询哪个指标？",
            examples=examples,
        ),
        SlotSpec(
            name="time_range",
            required=True,
            on_missing="ask",
            question="请补充有明确年份的统计日期、月末时点或起止范围。",
            examples=("2026年4月末", "2026年4月1日至2026年4月30日"),
        ),
    )


def _error_clarification(error: ExtractionError, question: str) -> Clarification | None:
    if error.code == "EXTRACTION_MULTI_METRIC_UNSUPPORTED":
        return Clarification(
            reason="multi_metric",
            question="当前仅支持单指标，请指定本次要查询的一个指标。",
            slot="metric",
            source_error_code=error.code,
            source_error_path=error.path,
        )
    if (
        error.code == "EXTRACTION_UNRESOLVED"
        and isinstance(error.fragments, tuple)
        and error.fragments
        and all(isinstance(f, str) and f.strip() and f in question for f in error.fragments)
    ):
        # 原片段是引用数据，不按关键词重新分类，也不把其余片段标记为已解决。
        return Clarification(
            reason="unresolved",
            question=f"请明确「{error.fragments[0]}」的具体含义，并补充完整问题后重新提交。",
            fragments=error.fragments,
            source_error_code=error.code,
            source_error_path=error.path,
        )
    return None


async def prepare_question(question: str, catalog: SemanticCatalog, *, llm: LLMClient) -> PreparationResult:
    """一次抽取 + 确定性检查，不修复、不补答合并、不编译、不执行。

    仅两种已登记语义错误转成追问，其余异常原样传播；slots_complete
    只允许调用方继续尝试既有下游检查，绝不代替它们。
    """
    try:
        extraction = await extract_intent(question, catalog, llm=llm)
    except ExtractionError as error:
        clarification = _error_clarification(error, question)
        if clarification is None:
            raise
        return PreparationResult(
            status="needs_clarification",
            extraction=None,
            missing_slots=(),
            clarification=clarification,
        )

    intent = extraction.intent
    check = check_slots(
        {"metric": intent.metric, "time_range": intent.time_range},
        slots=_required_slots(catalog),
    )
    if check.complete:
        return PreparationResult(
            status="slots_complete",
            extraction=extraction,
            missing_slots=(),
            clarification=None,
        )
    next_question = check.next_question
    assert next_question is not None  # 专用规格均有固定问题，missing 非空时必有问题。
    return PreparationResult(
        status="needs_clarification",
        extraction=extraction,
        missing_slots=tuple(spec.name for spec in check.missing),
        clarification=Clarification(
            reason="missing_slot",
            question=next_question,
            slot=check.missing[0].name,
        ),
    )
