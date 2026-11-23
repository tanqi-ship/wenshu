"""黄金集门禁的测试：让 ``uv run pytest`` 自动守住每一形状。

三种测试，缺一层都不算守住：

1. **参数化真跑**：``eval/golden/cases.yaml`` 里的每一条都必须通过
   —— 加一条用例不需要写新测试（这是 M1.5 的目标）
2. **元数据纪律**：每条用例必须有 ``covers`` / ``why_hard`` / 正确的 id 格式
   —— 对应项目纪律「每加一种形状必须同时交三样」
3. **门禁自己会不会红**：故意造一条错的黄金 SQL，断言它被判失败
   —— 否则"永远绿"的门禁比没有门禁更糟

打 ``@pytest.mark.golden`` 标记（见 ``pyproject.toml`` 的 markers）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import MetricDef
from scripts.golden import (
    GoldenConfigError,
    cases_from_payload,
    evaluate_case,
    load_cases,
)

#: 用 __file__ 定位，避免依赖 pytest 的 cwd。
GOLDEN_DIR = Path(__file__).resolve().parents[2] / "eval" / "golden"
CASES = load_cases(GOLDEN_DIR)


def _resolve(case_id: str) -> str:
    """pytest 的用例 id 用这个小工具生成，失败时一眼能看出是哪条。"""
    return case_id


# ---------------------------------------------------------------------------
# 1. 参数化真跑：每条黄金用例都必须通过
# ---------------------------------------------------------------------------


@pytest.mark.golden
@pytest.mark.parametrize("case", CASES, ids=[_resolve(case.id) for case in CASES])
def test_golden_case_passes(case) -> None:  # type: ignore[no-untyped-def]
    """编译 → 比对 → 校验，三步都要过。

    失败时把 diff 完整打出来，而不是只说一句 assert False ——
    门禁的价值有一半在"失败信息能直接定位"。
    """
    outcome = evaluate_case(case, semantic_catalog(), validation_catalog())

    if outcome.compile_error:
        pytest.fail(f"{case.id} 编译失败：{outcome.compile_error}")
    if outcome.comparison is not None and not outcome.matches:
        pytest.fail(f"{case.id} 与黄金 SQL 不一致：\n{outcome.comparison.render()}")
    if not outcome.validation_ok:
        pytest.fail(f"{case.id} 的产出未通过 L4：{', '.join(outcome.validation_codes)}")

    assert outcome.ok


# ---------------------------------------------------------------------------
# 2. 元数据纪律（对应「每加一种形状必须交三样」）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[_resolve(case.id) for case in CASES])
def test_case_declares_what_it_covers(case) -> None:  # type: ignore[no-untyped-def]
    """没有 covers 的用例，CI 挂了也定位不到"哪条规则退化了"。"""
    assert case.covers, f"{case.id} 没有 covers：这条用例测不出东西"
    assert case.shape, f"{case.id} 没有 shape 标签"


@pytest.mark.parametrize("case", CASES, ids=[_resolve(case.id) for case in CASES])
def test_case_explains_why_it_is_hard(case) -> None:  # type: ignore[no-untyped-def]
    """``why_hard`` 少于 20 字，说明这条用例的难点没写清。

    与 ``scripts/validate_cases.py`` 对评测集的要求保持一致。
    """
    assert len(case.why_hard) >= 20, f"{case.id} 的 why_hard 太短：{case.why_hard!r}"


def test_case_ids_are_golden_prefixed() -> None:
    """黄金集用 ``G-``，评测集用 ``AQ-``，两者不混编号。

    两者曾经撞号：同一个 ``AQ-001`` 在黄金集里是不良贷款余额、
    在评测集里是贷款余额。
    """
    assert CASES, "黄金集不能为空"
    assert all(case.id.startswith("G-") for case in CASES)


# ---------------------------------------------------------------------------
# 3. 门禁自己会不会红
# ---------------------------------------------------------------------------


def _fake_case(tmp_path: Path, sql: str, metric: str = "不良贷款余额"):  # type: ignore[no-untyped-def]
    sql_path = tmp_path / "G-900.sql"
    sql_path.write_text(sql, encoding="utf-8")
    payload = [
        {
            "id": "G-900",
            "shape": "month_end_single_point",
            "intent": {"metric": metric, "time": {"grain": "month_end", "points": ["2026-04-30"]}},
            "golden": "G-900.sql",
        }
    ]
    return cases_from_payload(payload, directory=tmp_path, source="test")[0]


def test_gate_fails_when_golden_differs(tmp_path: Path) -> None:
    """黄金 SQL 与引擎产出一旦不同，必须判失败（而不是永远绿）。"""
    case = _fake_case(
        tmp_path,
        """
        SELECT SUM(t1.current_balnc_cny) AS npl_balance
        FROM qa_loan_acc_detail AS t1
        WHERE t1.etl_dt = '2026-03-31'
        """,
    )
    outcome = evaluate_case(case, semantic_catalog(), validation_catalog())

    assert not outcome.ok
    assert outcome.failure_kind == "与黄金 SQL 不一致"
    assert outcome.comparison is not None
    assert outcome.comparison.diff


def test_gate_reports_compile_failure(tmp_path: Path) -> None:
    """指标不在语义层里时，失败原因必须是"编译失败"，而且要带 stage/code。"""
    case = _fake_case(tmp_path, "SELECT 1", metric="贷款迁徙率")
    outcome = evaluate_case(case, semantic_catalog(), validation_catalog())

    assert not outcome.ok
    assert outcome.failure_kind == "编译失败"
    assert outcome.compile_error == "symbol_table/UNKNOWN_METRIC"


def test_gate_reports_validation_problem(tmp_path: Path) -> None:
    """与黄金 SQL 一致、却违反 L4 规则时，失败原因必须是"L4 校验未通过"。

    这个测试钉住一条设计：**比对与校验互相独立，两个结论都要看。**

    怎么造出这个局面：让语义层的公式引用一个不存在的列
    （``SUM({tbl}.no_such_column)``）。编译器只看"公式能不能解析"，
    不管列存不存在（见 ``compiler/metrics.py`` 头部的两条纪律），
    所以它照样产出一条合法 SQL；列名错由 L4 的 ``UNKNOWN_COLUMN`` 抓。
    黄金 SQL 写成和产出一模一样，于是 ``matches=True`` 而 ``validation_ok=False``。
    """
    bad_semantic = SemanticCatalog.build(
        metrics=[
            MetricDef(
                code="npl_balance",
                name="不良贷款余额",
                formula="SUM({tbl}.no_such_column)",
                source_tables=(TABLE,),
            )
        ]
    )
    case = _fake_case(
        tmp_path,
        """
        SELECT SUM(t1.no_such_column) AS npl_balance
        FROM qa_loan_acc_detail AS t1
        WHERE t1.etl_dt = '2026-04-30'
        """,
    )
    outcome = evaluate_case(case, bad_semantic, validation_catalog())

    assert outcome.matches, "这条假用例的黄金 SQL 与产出应当一致"
    assert not outcome.validation_ok
    assert outcome.failure_kind == "L4 校验未通过"
    assert "UNKNOWN_COLUMN" in outcome.validation_codes


# ---------------------------------------------------------------------------
# 4. 加载器 fail-closed：配置写错必须响
# ---------------------------------------------------------------------------


def _payload(**overrides: object) -> list[dict[str, object]]:
    base: dict[str, object] = {
        "id": "G-900",
        "shape": "month_end_single_point",
        "intent": {"metric": "贷款余额", "time": {"grain": "month_end", "points": ["2026-04-30"]}},
        "golden": "G-900.sql",
    }
    base.update(overrides)
    return [base]


@pytest.mark.parametrize(
    ("label", "payload", "match"),
    [
        ("缺字段", [{"id": "G-900", "shape": "x"}], "缺少字段"),
        ("未知键", _payload(id="G-900", shap="x"), "不认识的键"),
        ("id 格式", _payload(id="AQ-001", golden="x.sql"), "id 格式不对"),
        ("covers 格式", _payload(covers=["C-100"]), "无法识别的编号"),
        ("intent 写错", _payload(intent={"metric": "x", "grian": "month_end"}), "intent 写错了"),
        ("顶层不是列表", {"id": "G-900"}, "应该是一个用例列表"),
    ],
)
def test_bad_payload_raises(tmp_path: Path, label: str, payload: object, match: str) -> None:
    (tmp_path / "G-900.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "x.sql").write_text("SELECT 1", encoding="utf-8")

    with pytest.raises(GoldenConfigError) as excinfo:
        cases_from_payload(payload, directory=tmp_path, source="test")

    assert match in str(excinfo.value), f"{label}：报错信息里应该提到「{match}」"


def test_duplicate_ids_raise(tmp_path: Path) -> None:
    (tmp_path / "G-900.sql").write_text("SELECT 1", encoding="utf-8")
    payload = [*_payload(), *_payload()]

    with pytest.raises(GoldenConfigError, match="id 重复"):
        cases_from_payload(payload, directory=tmp_path, source="test")


def test_missing_golden_file_raises(tmp_path: Path) -> None:
    with pytest.raises(GoldenConfigError, match="黄金 SQL 不存在"):
        cases_from_payload(_payload(golden="不存在.sql"), directory=tmp_path, source="test")


def test_orphan_golden_sql_raises(tmp_path: Path) -> None:
    """有 .sql 却没登记 —— 否则"加了一条黄金 SQL 却没人跑"会静默发生。"""
    (tmp_path / "G-900.sql").write_text("SELECT 1", encoding="utf-8")
    (tmp_path / "G-901.sql").write_text("SELECT 1", encoding="utf-8")

    with pytest.raises(GoldenConfigError, match="没有登记这些黄金 SQL"):
        cases_from_payload(_payload(), directory=tmp_path, source="test")


def test_scope_is_threaded_into_compilation(tmp_path: Path) -> None:
    """``case.scope`` 必须真的传进编译器，否则权限形状的门禁是假的。

    这是"双向证明"，只断言一侧没有意义：

    * 有 scope   → 产出带 ``AND t1.org_no IN (...)`` → 与黄金 SQL 一致
    * 无 scope   → 产出不带权限条件           → 与黄金 SQL 不一致

    如果没有第二个断言，那么"把 scope 写在 dataclass 里却忘了传下去"
    这个 bug 会完全静默 —— 因为门禁只会看到"我传了 scope，它通过了"。
    这条与 ``test_scope_loading.py`` 里"没有取值的策略不许被丢掉"是同一个主题：
    权限的错不会自己喊，只能靠对照钉住。
    """
    (tmp_path / "G-900.sql").write_text(
        """
        SELECT SUM(t1.current_balnc_cny) AS loan_balance
        FROM qa_loan_acc_detail AS t1
        WHERE t1.etl_dt = '2026-04-30' AND t1.org_no IN ('B001', 'B002')
        """,
        encoding="utf-8",
    )
    scope_config = {
        "tables": [TABLE],
        "row_policies": [{"table": TABLE, "column": "org_no", "operator": "in", "values": ["B001", "B002"]}],
    }

    with_scope = cases_from_payload(_payload(scope=scope_config), directory=tmp_path, source="test")[0]
    without_scope = cases_from_payload(_payload(), directory=tmp_path, source="test")[0]

    assert with_scope.scope is not None, "scope 没有被加载进来"
    assert without_scope.scope is None

    matched = evaluate_case(with_scope, semantic_catalog(), validation_catalog())
    assert matched.matches, matched.comparison.render() if matched.comparison else ""

    unmatched = evaluate_case(without_scope, semantic_catalog(), validation_catalog())
    assert not unmatched.matches, "没有传 scope 却仍然对上了，说明 scope 根本没进编译器"


def test_gate_checks_intent_even_when_wrong_sql_matches_golden(tmp_path: Path, monkeypatch) -> None:
    """结构对照不能代替规则：黄金 SQL 自己漏掉窗口时，L4 也必须拒绝。"""
    monthly = next(case for case in CASES if case.shape == "monthly_topn")
    sql = "SELECT org_no, SUM(current_balnc_cny) AS bal FROM qa_loan_acc_detail GROUP BY org_no ORDER BY bal DESC LIMIT 1"
    path = tmp_path / "G-900.sql"
    path.write_text(sql, encoding="utf-8")
    case = replace(monthly, id="G-900", sql_path=path)
    monkeypatch.setattr("scripts.golden.compile_sql", lambda *args, **kwargs: sql)
    outcome = evaluate_case(case, semantic_catalog(), validation_catalog())
    assert outcome.matches
    assert not outcome.ok
    assert "TOPN_MISSING_WINDOW" in outcome.validation_codes
