"""M2.1 验收：双时点对比（``comparison="delta"``）。

三层断言，与 M1 的验收口径一致：

1. **结构对不对** —— 三列、单 SELECT、无子查询，且时点条件在聚合**内部**
2. **算得对不对** —— 由黄金集 `eval/golden/G-008.sql` / `G-009.sql` 负责
3. **越界必失败** —— yoy / mom / rate、点数不对、比率型指标，全部显式报错

第 1 层里最关键的一条是 :func:`test_point_condition_is_inside_aggregate`：
把 CASE 套在聚合**外面**写出来的 SQL 语法同样合法、也能跑，但语义完全错
（SUM 先在整张表上聚合，再由 CASE 决定要不要）。这种错不报错、不告警，
只有结构断言和黄金 SQL 能发现。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, compile_sql
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import Filter, Intent, TimeRange
from app.engine.validation import validate_sql

CUR = date(2026, 4, 30)
BASE = date(2026, 1, 31)


def delta_intent(metric: str = "贷款余额") -> Intent:
    """当前时点在前、基准时点在后 —— 顺序是被钉住的契约。"""
    return Intent(
        metric=metric,
        time_range=TimeRange(grain="month_end", points=(CUR, BASE)),
        comparison="delta",
    )


# ---------------------------------------------------------------------------
# 1. 结构
# ---------------------------------------------------------------------------


def test_delta_produces_three_named_columns() -> None:
    """AQ-004 的输出约定：当前值、基准值、差值三列都要返回。"""
    select = compile_intent(delta_intent(), semantic_catalog())

    assert [projection.alias_or_name for projection in select.expressions] == [
        "cur_balance",
        "base_balance",
        "delta",
    ]


def test_delta_is_one_select_with_in_clause() -> None:
    """两个时点必须一条 SELECT 一次筛出（R-COMP-001）。"""
    select = compile_intent(delta_intent(), semantic_catalog())

    assert len(list(select.find_all(exp.Select))) == 1, "出现了嵌套 SELECT/子查询"
    where = select.args["where"].this
    assert isinstance(where.find(exp.In), exp.In), "WHERE 里应该是 IN (...)"


def test_point_condition_is_inside_aggregate() -> None:
    """★ 时点条件必须在 SUM 的参数里，而不是套在 SUM 外面。

    正确的：``SUM(CASE WHEN etl_dt = '...' THEN x ELSE 0 END)``
    错误的：``CASE WHEN etl_dt = '...' THEN SUM(x) ELSE 0 END``（语法合法、语义错）

    断言方式：每一个 ``SUM`` 的参数都必须是 CASE；并且**没有任何 CASE 里装着 SUM**
    （装着 SUM 就是"套在外面"那种写法）。
    """
    select = compile_intent(delta_intent(), semantic_catalog())

    sums = list(select.find_all(exp.Sum))
    assert len(sums) == 4, "当前值、基准值各一次，差值里再各一次 → 共 4 个 SUM"
    for summed in sums:
        assert isinstance(summed.this, exp.Case), "SUM 的参数不是 CASE，时点条件注入位置不对"

    for case in select.find_all(exp.Case):
        assert case.find(exp.Sum) is None, "有 CASE 把 SUM 包在里面了 —— 语义是错的"


def test_point_condition_reads_snapshot_column_from_policy() -> None:
    """快照列名来自 ValidationPolicy，不是硬编码的 etl_dt。"""
    select = compile_intent(delta_intent(), semantic_catalog())

    columns = {column.name for column in select.find_all(exp.Column)}
    assert "etl_dt" in columns


def test_nested_case_when_formula_has_its_own_case() -> None:
    """不良余额的公式自带 CASE，于是产出两层嵌套 CASE（见黄金集 G-009）。"""
    select = compile_intent(delta_intent("不良贷款余额"), semantic_catalog())

    outer_cases = [summed.this for summed in select.find_all(exp.Sum) if isinstance(summed.this, exp.Case)]
    assert outer_cases
    for outer in outer_cases:
        inner = outer.args["ifs"][0].args["true"]
        assert isinstance(inner, exp.Case), "内层应当是公式自带的 CASE"


def test_delta_keeps_where_order_time_then_business_then_policy() -> None:
    """时间 → 业务 → 权限，顺序仍然固定（ADR-0004）。"""
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="month_end", points=(CUR, BASE)),
        comparison="delta",
        filters=(Filter(dimension="机构", value="渝北支行"),),
    )
    scope = Scope.of(
        [TABLE],
        row_policies=(RowPolicy(table=TABLE, column="org_no", values=("B001", "B002"), operator="in"),),
    )
    select = compile_intent(intent, semantic_catalog(), scope=scope)

    rendered = [condition.sql(dialect="sqlite") for condition in _split_and(select.args["where"].this)]
    assert len(rendered) == 3
    assert rendered[0] == "t1.etl_dt IN ('2026-04-30', '2026-01-31')"
    assert rendered[1] == "t1.org_no = 'B001'"
    assert rendered[2] == "t1.org_no IN ('B001', 'B002')"


def test_delta_with_dimension_groups_by_code_column_only() -> None:
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="month_end", points=(CUR, BASE)),
        comparison="delta",
        dimensions=("机构",),
    )
    sql = compile_sql(intent, semantic_catalog())

    assert "GROUP BY" in sql
    group_by = sql.split("GROUP BY", 1)[1]
    assert "org_no" in group_by
    assert "org_nm" not in group_by, "名称列不许进 GROUP BY（R-NAME-001）"
    assert sql.count("MAX(t1.org_nm)") == 1


# ---------------------------------------------------------------------------
# 2. 合规：产出必须通过 L4
# ---------------------------------------------------------------------------


def test_delta_output_passes_l4() -> None:
    intent = delta_intent()
    sql = compile_sql(intent, semantic_catalog())
    result = validate_sql(sql, catalog=validation_catalog(), intent=intent)

    assert result.ok, [problem.render() for problem in result.problems]
    assert "COMPARISON_USES_SUBQUERY" not in result.codes


# ---------------------------------------------------------------------------
# 3. 越界必失败（每一条都对应一个"静默错"的入口）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("comparison", ["yoy", "mom", "rate"])
def test_unsupported_comparisons_are_rejected(comparison: str) -> None:
    """yoy / mom / rate 形状不同（跨年、跨月、除法），M2.1 不实现，且不许当成 delta。"""
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="month_end", points=(CUR, BASE)),
        comparison=comparison,  # type: ignore[arg-type]
    )
    with pytest.raises(CompileError) as excinfo:
        compile_sql(intent, semantic_catalog())

    assert excinfo.value.stage == "intent"
    assert excinfo.value.code == "COMPARISON_UNSUPPORTED"
    assert comparison in excinfo.value.message


@pytest.mark.parametrize(
    ("label", "points"),
    [
        ("只有一个时点", (CUR,)),
        ("三个时点", (CUR, BASE, date(2025, 12, 31))),
        ("没有时点", ()),
    ],
)
def test_wrong_point_count_is_rejected(label: str, points: tuple[date, ...]) -> None:
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="month_end", points=points),
        comparison="delta",
    )
    with pytest.raises(CompileError) as excinfo:
        compile_sql(intent, semantic_catalog())

    assert excinfo.value.code == "COMPARISON_POINT_COUNT", label


def test_two_points_without_comparison_is_rejected() -> None:
    """★ 最危险的一条：两个时点却没说怎么比。

    如果放过去，单时点形状会产出一条 `SUM(...)` 配 `WHERE etl_dt IN (...)` 的 SQL ——
    也就是**把两个时点的余额加在一起**。跑得通、不报错、数字还挺像。
    """
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="month_end", points=(CUR, BASE)),
    )
    with pytest.raises(CompileError) as excinfo:
        compile_sql(intent, semantic_catalog())

    assert excinfo.value.stage == "intent"
    assert excinfo.value.code == "MULTI_POINT_WITHOUT_COMPARISON"


def test_delta_on_range_grain_is_rejected() -> None:
    """区间是闭区间，没有"当前/基准"两个点，不能套 delta。"""
    intent = Intent(
        metric="贷款余额",
        time_range=TimeRange(grain="range", start=BASE, end=CUR),
        comparison="delta",
    )
    with pytest.raises(CompileError) as excinfo:
        compile_sql(intent, semantic_catalog())

    assert excinfo.value.code == "COMPARISON_UNSUPPORTED"
    assert "month_end" in excinfo.value.message


def test_non_additive_metric_is_rejected() -> None:
    """不良率（顶层是除法）不能做差值：分段比率相减没有业务含义。"""
    with pytest.raises(CompileError) as excinfo:
        compile_sql(delta_intent("不良贷款率"), semantic_catalog())

    assert excinfo.value.stage == "type_check"
    assert excinfo.value.code == "DELTA_NOT_ADDITIVE"
    assert "SUM" in excinfo.value.message


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _split_and(node: exp.Expression) -> list[exp.Expression]:
    """把 ``a AND b AND c`` 铺平成 [a, b, c]（AND 是二叉树）。"""
    if isinstance(node, exp.And):
        return [*_split_and(node.this), *_split_and(node.expression)]
    return [node]
