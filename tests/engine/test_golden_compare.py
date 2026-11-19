"""``scripts/golden.py`` 的单测：归一化「该忽略什么、不该忽略什么」。

两类断言缺一不可：

1. **该忽略的必须忽略** —— 否则黄金集会因为排版噪音变成"天天红"，
   红了几天之后就没人看了，门禁退化成噪音。
2. **不该忽略的必须不忽略** —— 否则门禁对真错误视而不见，
   比没有门禁更糟：它会给人一种"已经守住了"的错觉。

第 2 类里最重要的是 ``test_missing_from_clause_is_detected``：
它正是 M1 那个 ``from_``/``from`` 坑的守门人。那个 bug L4 抓不到
（``SELECT 1`` 也是合法 SQL），只能靠黄金集比对和结构断言兜住。
"""

from __future__ import annotations

import pytest

from scripts.golden import Comparison, SqlParseError, canonical_sql, compare

# ---------------------------------------------------------------------------
# 1. 该忽略的必须忽略
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "left", "right"),
    [
        ("标识符大小写", "SELECT SUM(X) AS Y FROM T WHERE A = 1", "select sum(x) as y from t where a = 1"),
        ("空白与换行", "SELECT a FROM t", "select a\n  from   t"),
        ("结尾分号", "SELECT a FROM t;", "SELECT a FROM t"),
        ("行注释", "-- 黄金 SQL 头部说明\n-- 第二行\nSELECT a FROM t", "SELECT a FROM t"),
        ("行内块注释", "SELECT a /* 行内 */ FROM t", "SELECT a FROM t"),
        ("尾部注释", "SELECT a FROM t  -- 尾注", "SELECT a FROM t"),
    ],
)
def test_allowed_differences_are_ignored(label: str, left: str, right: str) -> None:
    result = compare(left, right)
    assert result.matches, f"{label} 应该被忽略，但被判为不一致：\n{result.render()}"


def test_canonical_output_is_single_line() -> None:
    """归一化结果必须是单行：断言与 diff 都基于它。"""
    canonical = canonical_sql("SELECT\n  a,\n  b\nFROM t")
    assert "\n" not in canonical
    assert canonical == "SELECT a, b FROM t"


# ---------------------------------------------------------------------------
# 2. 不该忽略的必须不忽略
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "left", "right"),
    [
        # 字面量的大小写在业务上有意义：机构号 'B001' 与 'b001' 不是同一个值。
        ("字面量大小写", "SELECT a FROM t WHERE c = 'B001'", "SELECT a FROM t WHERE c = 'b001'"),
        # 形状契约：口径取值集合变了必须被看见（'50' 是损失类，删掉就算错了）。
        ("IN 列表顺序", "SELECT a FROM t WHERE c IN ('30','40')", "SELECT a FROM t WHERE c IN ('40','30')"),
        (
            "IN 少一个取值",
            "SELECT a FROM t WHERE c IN ('30','40','50')",
            "SELECT a FROM t WHERE c IN ('30','40')",
        ),
        # 合成顺序是契约的一部分：时间 → 业务 → 权限（compiler/__init__.py:237）。
        ("AND 两侧顺序", "SELECT a FROM t WHERE x = 1 AND y = 2", "SELECT a FROM t WHERE y = 2 AND x = 1"),
        ("单时点 vs 多时点", "SELECT a FROM t WHERE d = '1'", "SELECT a FROM t WHERE d IN ('1','2')"),
        # ★ from_/from 那个坑的守门人
        ("缺 FROM 子句", "SELECT SUM(x) AS y FROM t", "SELECT SUM(x) AS y"),
        ("表别名不同", "SELECT x FROM t AS t1", "SELECT x FROM t AS t2"),
        ("缺 GROUP BY", "SELECT a, SUM(x) AS s FROM t GROUP BY a", "SELECT a, SUM(x) AS s FROM t"),
    ],
)
def test_meaningful_differences_are_reported(label: str, left: str, right: str) -> None:
    result = compare(left, right)
    assert not result.matches, f"{label} 必须被发现，但被判为一致。"
    assert result.diff, "不一致时必须给出 diff，否则人没法排查。"


def test_missing_from_clause_is_detected() -> None:
    """回归测试：FROM 整段丢失是合法 SQL，L4 抓不到，只能靠这里。

    M1 的真实踩坑：``exp.Select(**{"from": ...})`` 写错键名不报错，
    静默吞掉整个 FROM。``SELECT SUM(x) AS y`` 依然是合法 SQL。
    """
    produced_without_from = "SELECT SUM(x) AS y"
    golden_with_from = "SELECT SUM(x) AS y FROM qa_loan_acc_detail AS t1"

    result = compare(golden_with_from, produced_without_from)
    assert not result.matches
    assert any("FROM" in line for line in result.diff)


# ---------------------------------------------------------------------------
# 3. 解析失败必须抛错，不能混进"不一致"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "bad_sql"),
    [
        ("语法错误", "SELECT FROM WHERE"),
        ("多语句", "SELECT a FROM t; SELECT b FROM t"),
        ("空字符串", "   "),
    ],
)
def test_unparseable_sql_raises(label: str, bad_sql: str) -> None:
    """解析失败与"对不上"必须分开：前者是坏了，后者是算错了。"""
    with pytest.raises(SqlParseError):
        compare(bad_sql, "SELECT a FROM t")
    with pytest.raises(SqlParseError):
        canonical_sql(bad_sql)


# ---------------------------------------------------------------------------
# 4. 结果对象本身
# ---------------------------------------------------------------------------


def test_comparison_carries_both_sides() -> None:
    result = compare("SELECT a FROM t", "SELECT b FROM t")
    assert isinstance(result, Comparison)
    assert result.golden == "SELECT a FROM t"
    assert result.produced == "SELECT b FROM t"
    assert result.render() == "\n".join(result.diff)


def test_matching_comparison_has_empty_diff() -> None:
    result = compare("select a from t", "SELECT a FROM t")
    assert result.matches
    assert result.diff == ()
    assert result.render() == "一致"
