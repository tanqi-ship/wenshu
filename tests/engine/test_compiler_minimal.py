"""M1 验收：编译器最小闭环。

三层断言，缺一层都不算通过：

1. **与黄金 SQL 一致** —— 结构等价（忽略排版），证明"算对了"
2. **通过 L4 校验** —— 证明"合规"
3. **反例必须失败** —— 证明"真的在检查"，而不是永远返回 ok

三个反例分别打在三个 stage 上：``symbol_table`` / ``intent`` / ``scope``。
它们对应三种完全不同的人该修：配置维护者 / 用户补充信息 / 权限配置。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.engine.compiler import CompileError, compile_intent, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import Intent, TimeRange
from app.engine.validation import validate_sql

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "eval" / "golden"


def normalize_sql(sql: str) -> str:
    """比对用：折叠空白、统一大小写、去掉语句尾分号。

    比的是 SQL 语义，不是排版。排版由 formatter 负责，不该进断言。

    注意：``scripts/golden.py::compare`` 是同一件事的更严格版本
    （解析成树再归一化，还能出 diff）。黄金集的正式比对必须走那边；
    这里保留这个简单实现，是因为本文件只需要"是不是同一句话"这一个判断。
    """
    stripped = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return " ".join(stripped.split()).lower().rstrip(";")


def intent_npl() -> Intent:
    return Intent(metric="不良贷款余额", time_range=month_end_range(2026, 4))


# ---------------------------------------------------------------------------
# 1. 与黄金 SQL 一致
# ---------------------------------------------------------------------------


def test_aq001_matches_golden_sql() -> None:
    golden = (GOLDEN_DIR / "G-001.sql").read_text(encoding="utf-8")
    produced = compile_sql(intent_npl(), semantic_catalog())
    assert normalize_sql(produced) == normalize_sql(golden)


def test_metric_resolved_by_synonym() -> None:
    """「不良余额」应解析到同一个指标——同义词是语义层的职责，不是模型的。"""
    by_synonym = compile_sql(
        Intent(metric="不良余额", time_range=month_end_range(2026, 4)),
        semantic_catalog(),
    )
    by_name = compile_sql(intent_npl(), semantic_catalog())
    assert normalize_sql(by_synonym) == normalize_sql(by_name)


def test_formula_without_placeholder_gets_qualified() -> None:
    """不带占位符的历史公式，列名必须被补上表别名。"""
    sql = compile_sql(
        Intent(metric="loan_balance", time_range=month_end_range(2026, 4)),
        semantic_catalog(),
    )
    assert "t1.current_balnc_cny" in sql


# ---------------------------------------------------------------------------
# 2. 编译产物必须通过 L4 校验
# ---------------------------------------------------------------------------


def test_compiled_sql_passes_validation() -> None:
    sql = compile_sql(intent_npl(), semantic_catalog())
    result = validate_sql(sql, catalog=validation_catalog())
    assert result.ok, [problem.render() for problem in result.problems]


def test_from_clause_present() -> None:
    """回归测试：FROM 曾因 sqlglot 的 arg key 写成 ``from`` 而静默丢失。

    这个 bug 特别阴：``SELECT 1`` 也是合法 SQL，所以 L4 的规则校验抓不到，
    只有结构断言或黄金集比对能发现。
    """
    statement = compile_intent(intent_npl(), semantic_catalog())
    assert statement.args.get("from_") is not None
    assert "FROM qa_loan_acc_detail" in statement.sql(dialect="sqlite", pretty=True)


# ---------------------------------------------------------------------------
# 3. 维度分组：名称字段必须走 MAX，绝不进 GROUP BY（R-NAME-001）
# ---------------------------------------------------------------------------


def test_dimension_groups_by_code_and_maxes_display_name() -> None:
    sql = compile_sql(
        Intent(
            metric="loan_balance",
            time_range=month_end_range(2026, 4),
            dimensions=("机构",),
        ),
        semantic_catalog(),
    )
    assert "MAX(t1.org_nm)" in sql
    assert "GROUP BY" in sql
    # GROUP BY 里只能是编号列
    group_by = sql.split("GROUP BY", 1)[1]
    assert "org_no" in group_by
    assert "org_nm" not in group_by

    result = validate_sql(sql, catalog=validation_catalog())
    assert result.ok
    assert "NAME_FIELD_IN_GROUP_BY" not in result.codes


# ---------------------------------------------------------------------------
# 4. 权限：注入点唯一，且 fail-closed
# ---------------------------------------------------------------------------


def test_row_policy_injected_into_where() -> None:
    scope = Scope.of(
        [TABLE],
        row_policies=(
            RowPolicy(
                table=TABLE,
                column="org_no",
                values=("B001", "B002"),
                operator="in",
            ),
        ),
    )
    sql = compile_sql(intent_npl(), semantic_catalog(), scope=scope)
    assert "t1.org_no IN ('B001', 'B002')" in sql


def test_row_policy_without_values_is_fail_closed() -> None:
    """一条没有取值的策略必须生成恒假条件，绝不能被跳过。

    静默跳过 = 越权，这是最危险的一种退化。
    """
    scope = Scope.of([TABLE], row_policies=(RowPolicy(table=TABLE, column="org_no"),))
    sql = compile_sql(intent_npl(), semantic_catalog(), scope=scope)
    normalized = normalize_sql(sql)
    assert "1 = 0" in normalized or "1=0" in normalized


def test_table_out_of_scope_raises_scope_error() -> None:
    with pytest.raises(CompileError) as excinfo:
        compile_sql(intent_npl(), semantic_catalog(), scope=Scope.of(["unrelated_table"]))
    assert excinfo.value.stage == "scope"
    assert excinfo.value.code == "METRIC_OUT_OF_SCOPE"


def test_validation_catches_out_of_scope_sql() -> None:
    """L4 独立于 L3：即使编译器放行，校验器也要能自己判定越权。"""
    sql = compile_sql(intent_npl(), semantic_catalog())
    result = validate_sql(sql, catalog=validation_catalog(), scope=Scope.of(["other_table"]))
    assert not result.ok
    assert "TABLE_NOT_IN_SCOPE" in result.codes


# ---------------------------------------------------------------------------
# 5. 反例：三种失败必须落在三个不同的 stage
# ---------------------------------------------------------------------------


def test_unknown_metric_is_symbol_table_error() -> None:
    with pytest.raises(CompileError) as excinfo:
        compile_sql(
            Intent(metric="贷款迁徙率", time_range=month_end_range(2026, 4)),
            semantic_catalog(),
        )
    assert excinfo.value.stage == "symbol_table"
    assert excinfo.value.code == "UNKNOWN_METRIC"


def test_missing_time_point_is_intent_error() -> None:
    """时间没解析出具体日期时必须回 L2 澄清，编译器**不许猜**月末几号。"""
    unparsed = TimeRange(grain="month_end", raw="2026年4月末")
    with pytest.raises(CompileError) as excinfo:
        compile_sql(Intent(metric="不良贷款余额", time_range=unparsed), semantic_catalog())
    assert excinfo.value.stage == "intent"
    assert excinfo.value.code == "TIME_UNRESOLVED"


def test_no_time_at_all_is_intent_error() -> None:
    with pytest.raises(CompileError) as excinfo:
        compile_sql(Intent(metric="不良贷款余额"), semantic_catalog())
    assert excinfo.value.stage == "intent"


def test_month_end_handles_short_month() -> None:
    """月末由 calendar 算：2 月不是 28 就是 29，这类事代码比模型可靠。"""
    assert month_end_range(2026, 2).points == (date(2026, 2, 28),)
    assert month_end_range(2024, 2).points == (date(2024, 2, 29),)
    assert month_end_range(2026, 4).points == (date(2026, 4, 30),)
