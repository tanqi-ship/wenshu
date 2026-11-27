"""评测判定：抽取与执行两套共用的规则。

抽取判定照搬 ``scripts/evaluate.py`` 的现有逻辑，不引入第二种判法。
执行判定按 Spec 的结果形状：标量、按行键映射、按列名映射、多行；
另加数据缺失与不可答两条行为题。全部是纯函数，不调用模型、不执行 SQL。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.engine.clarify.session.models import SessionResult
from app.engine.compiler import CompileError, compile_sql
from app.engine.execution import ExecutionError, QueryRows
from app.engine.query import QueryAnswer, QueryRejection, QueryValidationError
from app.engine.semantic.bank_mini import semantic_catalog, validation_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Intent, intent_from_mapping
from app.engine.understanding import ExtractionError
from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog

#: 数值容差。与 Spec 一致：1e-6 以内算相等。
TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class Verdict:
    """一次判定的结果；失败时必带 stage 与稳定 code。"""

    ok: bool
    stage: str = ""
    code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _ok(**details: Any) -> Verdict:
    return Verdict(ok=True, details=dict(details))


def _fail(stage: str, code: str, message: str, **details: Any) -> Verdict:
    return Verdict(ok=False, stage=stage, code=code, message=message, details=dict(details))


def _numbers_close(actual: object, expected: float) -> bool:
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return abs(float(actual) - expected) <= TOLERANCE


def match_scalar(rows: QueryRows, expected: float) -> bool:
    """标量：首行至少有一个数值单元格在容差内等于预期。"""
    if not rows.rows:
        return False
    return any(_numbers_close(cell, float(expected)) for cell in rows.rows[0])


def match_by_row_key(rows: QueryRows, expected: Mapping[str, object]) -> bool:
    """按行键映射：每个键对应唯一一行，行数相等，其余单元格有一个数值匹配。"""
    if not rows.columns or len(rows.rows) != len(expected):
        return False
    used: set[int] = set()
    for key, value in expected.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        matches = [index for index, row in enumerate(rows.rows) if row and str(row[0]) == str(key)]
        if len(matches) != 1:
            return False
        index = matches[0]
        used.add(index)
        if not any(_numbers_close(cell, float(value)) for cell in rows.rows[index][1:]):
            return False
    return len(used) == len(expected)


def match_by_column_name(rows: QueryRows, expected: Mapping[str, object]) -> bool:
    """按列名映射：首行各指定列在容差内等于预期。"""
    if not rows.rows or not expected:
        return False
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in expected.values()):
        return False
    try:
        indexes = {str(key): rows.columns.index(str(key)) for key in expected}
    except ValueError:
        return False
    first = rows.rows[0]
    return all(_numbers_close(first[indexes[str(key)]], float(value)) for key, value in expected.items())


def match_rows(rows: QueryRows, expected: Sequence[Mapping[str, object]]) -> bool:
    """多行：行数相等，按顺序逐行按列名比对；数值用容差，其余精确相等。"""
    if len(rows.rows) != len(expected):
        return False
    for actual, spec in zip(rows.rows, expected, strict=True):
        for key, value in spec.items():
            name = str(key)
            if name not in rows.columns:
                return False
            cell = actual[rows.columns.index(name)]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not _numbers_close(cell, float(value)):
                    return False
            elif cell != value:
                return False
    return True


def _matches_result(rows: QueryRows, expected: object) -> bool:
    if isinstance(expected, bool):
        return False
    if isinstance(expected, (int, float)):
        return match_scalar(rows, float(expected))
    if isinstance(expected, Sequence) and not isinstance(expected, str):
        return match_rows(rows, expected)
    if isinstance(expected, Mapping):
        keys = {str(key) for key in expected}
        if rows.columns and keys <= set(rows.columns):
            return match_by_column_name(rows, expected)
        return match_by_row_key(rows, expected)
    return False


def _strip_raw(intent: Intent) -> Intent:
    """比较前去掉所有 raw 展示字段；它们不属于抽取契约。"""
    time_range = replace(intent.time_range, raw="") if intent.time_range is not None else None
    filters = tuple(replace(item, raw="") for item in intent.filters)
    return replace(intent, time_range=time_range, filters=filters, raw={})


def judge_extraction(
    case: Mapping[str, Any],
    *,
    intent: Intent | None = None,
    error: ExtractionError | None = None,
    semantic: SemanticCatalog | None = None,
    physical: Catalog | None = None,
) -> Verdict:
    """判定一条抽取样本；规则与离线评测原有逻辑一致。"""
    expected_error = case.get("expected_error")
    if error is not None:
        if expected_error == error.code and tuple(case.get("expected_fragments", ())) == error.fragments:
            return _ok(expected=expected_error)
        return _fail("understanding", error.code, "抽取错误与手写预期不一致。")
    if intent is None:
        return _fail("understanding", "MISSING_INTENT", "抽取结果为空。")
    if expected_error:
        return _fail("understanding", "UNEXPECTED_SUCCESS", "预期抽取失败，但模型回放成功。")

    expected_intent = intent_from_mapping(case["expected"])
    if _strip_raw(intent) != _strip_raw(expected_intent):
        return _fail("understanding", "INTENT_MISMATCH", "Intent 与手写预期不一致。")

    semantic = semantic or semantic_catalog()
    physical = physical or validation_catalog()

    if case.get("compile_error"):
        try:
            compile_sql(intent, semantic)
        except CompileError as exc:
            if exc.code == case["compile_error"]:
                return _ok(expected=case["compile_error"])
            return _fail("compiler", exc.code, "预期编译错误码不一致。")
        return _fail("compiler", "UNEXPECTED_SUCCESS", "预期编译失败，但编译成功。")

    if case.get("compile"):
        sql = compile_sql(intent, semantic)
        checked = validate_sql(sql, catalog=physical, intent=intent)
        if checked.ok:
            return _ok()
        return _fail("validation", checked.codes[0], "抽取结果生成的 SQL 未通过 L4。")

    return _ok()


def _incomplete_session(result: object) -> bool:
    return isinstance(result, SessionResult) and result.status != "slots_complete"


def _judge_error(error: Exception) -> Verdict:
    if isinstance(error, ExtractionError):
        return _fail("understanding", error.code, "模型输出不符合抽取协议。")
    if isinstance(error, ExecutionError):
        return _fail("execution", error.code, "本地业务 SQLite 执行失败。")
    if isinstance(error, QueryValidationError):
        return _fail("validation", "QUERY_VALIDATION_FAILED", "查询未通过独立校验。")
    return _fail("compiler", type(error).__name__, "端到端样本执行异常。")


def _judge_behavior(behavior: str, result: object) -> Verdict:
    if behavior == "clarify_or_disclose":
        if isinstance(result, QueryAnswer):
            if result.session.note.strip():
                return _ok(disclosure=result.session.note)
            return _fail("data", "SILENT_ANSWER", "数据缺失时静默作答，没有披露。")
        if isinstance(result, QueryRejection):
            return _ok(stopped=result.code)
        if _incomplete_session(result):
            return _ok(status=result.status)
        return _fail("data", "UNEXPECTED_RESULT", "数据缺失时应追问、停止或披露后作答。")

    if behavior == "refuse_with_alternative":
        if isinstance(result, QueryRejection):
            return _ok(alternative=result.message)
        if _incomplete_session(result):
            return _ok(status=result.status)
        if isinstance(result, QueryAnswer):
            return _fail("data", "ANSWERED_UNANSWERABLE", "不可答问题返回了数据。")
        return _fail("data", "UNEXPECTED_RESULT", "不可答问题应拒绝。")

    return _fail("data", "UNKNOWN_BEHAVIOR", f"未知行为预期：{behavior}")


def judge_execution(
    case: Mapping[str, Any],
    result: object | None = None,
    *,
    error: Exception | None = None,
) -> Verdict:
    """判定一条执行样本：结果形状题或行为题。"""
    expected = case.get("expected") or {}
    if error is not None:
        return _judge_error(error)
    if result is None:
        return _fail("data", "NO_RESULT", "没有返回可判定结果。")

    behavior = expected.get("behavior")
    if behavior is not None:
        return _judge_behavior(str(behavior), result)
    if "result" not in expected:
        return _fail("data", "UNKNOWN_EXPECTATION", "用例缺少 result 或 behavior。")

    if isinstance(result, QueryAnswer):
        if _matches_result(result.result, expected["result"]):
            return _ok()
        return _fail("data", "RESULT_MISMATCH", "结果与手写预期不一致。")
    if isinstance(result, QueryRejection):
        return _fail("scope", result.code, result.message)
    if _incomplete_session(result):
        return _fail("clarification", "UNEXPECTED_CLARIFICATION", "预期出数，但返回了追问或停止。")
    return _fail("data", "NO_QUERY_ANSWER", "未返回可判定的查询结果。")
