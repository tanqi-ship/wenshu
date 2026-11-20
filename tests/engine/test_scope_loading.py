"""``app.engine.scope.scope_from_mapping`` 的单测。

这个加载器只服务一件事：让**黄金集用例**能写出 ``Scope``。
在此之前黄金集的 ``GoldenCase`` 里没有 scope 字段，于是权限相关的形状
（行级策略注入、fail-closed）只有单测，没有"从 Intent + Scope 到 SQL 文本"
的端到端对照 —— 而权限恰好是三个注入点里唯一"错了就是事故"的那个。

三条纪律，与 ``intent_from_mapping`` 保持一致：

1. **未知键直接报错**，不静默忽略（手滑写成 ``row_policy`` 会让门禁"通过"但测了个空）
2. **没有取值的策略是合法的**，表示"这些行都看不到"，不能被当成"没配策略"丢掉
3. 加载器**不做业务判断**（不校验表是否存在、列是否可见），那是编译器和校验器的活
"""

from __future__ import annotations

import pytest

from app.engine.scope import Scope, scope_from_mapping

TABLE = "qa_loan_acc_detail"


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


def test_minimal_scope_is_just_visible_tables() -> None:
    scope = scope_from_mapping({"tables": [TABLE]})

    assert scope.tables == frozenset({TABLE})
    assert scope.row_policies == ()
    assert scope.columns_for(TABLE) is None, "没配 columns 表示不限制列，不是一列都看不到"


def test_tables_are_lowercased() -> None:
    scope = scope_from_mapping({"tables": ["QA_LOAN_ACC_DETAIL"]})

    assert scope.tables == frozenset({TABLE})


def test_columns_become_lowercase_frozensets() -> None:
    scope = scope_from_mapping({"tables": [TABLE], "columns": {TABLE: ["ORG_NO", "Org_Nm"]}})

    assert scope.columns_for(TABLE) == frozenset({"org_no", "org_nm"})
    assert scope.columns_for("other_table") is None


def test_empty_column_list_means_nothing_visible() -> None:
    """``{表: []}`` 与"没配 columns"是两件事，必须能区分。

    前者是"这表一列都看不到"，后者是"不限制"。默认值搞反会变成静默越权。
    """
    scope = scope_from_mapping({"tables": [TABLE], "columns": {TABLE: []}})

    assert scope.columns_for(TABLE) == frozenset()


def test_row_policy_with_in_operator() -> None:
    scope = scope_from_mapping(
        {
            "tables": [TABLE],
            "row_policies": [
                {"table": TABLE, "column": "org_no", "operator": "in", "values": ["B001", "B002"]}
            ],
        }
    )

    (policy,) = scope.row_policies
    assert policy.table == TABLE
    assert policy.column == "org_no"
    assert policy.operator == "in"
    assert policy.values == ("B001", "B002")


def test_row_policy_operator_defaults_to_eq() -> None:
    scope = scope_from_mapping(
        {"tables": [TABLE], "row_policies": [{"table": TABLE, "column": "org_no", "values": ["B001"]}]}
    )

    assert scope.row_policies[0].operator == "eq"


def test_row_policy_without_values_is_kept_not_dropped() -> None:
    """★ 最关键的一条：没有取值的策略必须**原样保留**。

    它表示"这一行都看不到"，编译器会把它变成恒假条件 ``1 = 0``。
    如果加载器把它当成"没配策略"丢掉，SQL 里就没有权限条件 ——
    用户看到全行数据，而且不报错。这就是 fail-closed 与静默越权之间的那条线。
    """
    scope = scope_from_mapping({"tables": [TABLE], "row_policies": [{"table": TABLE, "column": "org_no"}]})

    assert len(scope.row_policies) == 1
    assert scope.row_policies[0].values == ()


def test_multiple_policies_for_multiple_tables() -> None:
    scope = scope_from_mapping(
        {
            "tables": [TABLE, "qa_writeoff_detail"],
            "row_policies": [
                {"table": TABLE, "column": "org_no", "values": ["B001"]},
                {"table": "qa_writeoff_detail", "column": "source_account_no", "values": ["A1"]},
            ],
        }
    )

    assert len(scope.policies_for(TABLE)) == 1
    assert len(scope.policies_for("qa_writeoff_detail")) == 1
    assert scope.policies_for("unknown_table") == ()


# ---------------------------------------------------------------------------
# 反例：配置写错必须响
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "payload", "expected"),
    [
        ("scope 顶层未知键", {"tables": [TABLE], "row_policy": []}, "不认识的键"),
        (
            "策略里的未知键",
            {"tables": [TABLE], "row_policies": [{"table": TABLE, "colum": "x"}]},
            "不认识的键",
        ),
        ("缺少 table", {"tables": [TABLE], "row_policies": [{"column": "org_no"}]}, "缺少 table"),
        ("缺少 column", {"tables": [TABLE], "row_policies": [{"table": TABLE}]}, "缺少 column"),
        (
            "operator 不认识",
            {"tables": [TABLE], "row_policies": [{"table": TABLE, "column": "c", "operator": "like"}]},
            "只能是 eq / in",
        ),
        (
            "values 写成单个字符串",
            {"tables": [TABLE], "row_policies": [{"table": TABLE, "column": "c", "values": "B001"}]},
            "取值列表",
        ),
        ("tables 为空", {"tables": []}, "不能为空"),
        ("tables 不是列表", {"tables": TABLE}, "表名列表"),
        ("整个 scope 不是映射", ["qa_loan_acc_detail"], "应该是一个映射"),
    ],
)
def test_bad_scope_config_raises(label: str, payload: object, expected: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        scope_from_mapping(payload)  # type: ignore[arg-type]

    assert expected in str(excinfo.value), f"{label} 的报错信息不达标：{excinfo.value}"


def test_scope_from_mapping_returns_scope_instance() -> None:
    assert isinstance(scope_from_mapping({"tables": [TABLE]}), Scope)
