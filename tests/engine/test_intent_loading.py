"""``intent_from_mapping`` 的单测。

这个加载器是黄金集与评测集的入口，所以它的失败方式必须**响**：
配置写错一个键，门禁要么红，要么明确报错 —— 绝不能静默忽略然后假装通过。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.engine.compiler import compile_sql
from app.engine.semantic import intent_from_mapping
from app.engine.semantic.bank_mini import semantic_catalog
from scripts.golden import compare

GOLDEN_SQL = Path("eval/golden/G-001.sql")


# ---------------------------------------------------------------------------
# 1. 正常路径
# ---------------------------------------------------------------------------


def test_minimal_mapping() -> None:
    intent = intent_from_mapping(
        {"metric": "不良贷款余额", "time": {"grain": "month_end", "points": ["2026-04-30"]}}
    )
    assert intent.metric == "不良贷款余额"
    assert intent.time_range is not None
    assert intent.time_range.grain == "month_end"
    assert intent.time_range.points == (date(2026, 4, 30),)
    assert intent.dimensions == ()
    assert intent.filters == ()
    assert intent.source_mode == "plain"


def test_range_time() -> None:
    intent = intent_from_mapping(
        {
            "metric": "贷款余额",
            "time": {"grain": "range", "start": "2026-01-31", "end": "2026-04-30"},
        }
    )
    assert intent.time_range is not None
    assert intent.time_range.start == date(2026, 1, 31)
    assert intent.time_range.end == date(2026, 4, 30)


def test_dimensions_and_filters() -> None:
    intent = intent_from_mapping(
        {
            "metric": "不良贷款余额",
            "time": {"grain": "month_end", "points": ["2026-04-30"]},
            "dimensions": ["机构", "产品"],
            "filters": [{"dimension": "机构", "value": "B001"}],
        }
    )
    assert intent.dimensions == ("机构", "产品")
    assert len(intent.filters) == 1
    assert intent.filters[0].dimension == "机构"
    assert intent.filters[0].value == "B001"
    assert intent.filters[0].operator == "eq"


def test_top_n_and_source_mode() -> None:
    intent = intent_from_mapping(
        {
            "metric": "贷款余额",
            "time": {"grain": "month", "points": ["2026-04-30"]},
            "top_n": 3,
            "top_n_grain": "month",
            "source_mode": "report",
        }
    )
    assert intent.top_n == 3
    assert intent.needs_monthly_topn is True
    assert intent.source_mode == "report"


def test_mapping_intent_compiles_to_golden_sql() -> None:
    """端到端冒烟：配置 → Intent → 编译 → 与黄金 SQL 比对。

    这条用例先把「加载器 + 编译器 + 比对器」串起来跑一次，
    免得等到 ``cases.yaml`` 接好之后才发现三者对不上。
    """
    intent = intent_from_mapping(
        {"metric": "不良贷款余额", "time": {"grain": "month_end", "points": ["2026-04-30"]}}
    )
    produced = compile_sql(intent, semantic_catalog())
    golden = GOLDEN_SQL.read_text(encoding="utf-8")

    result = compare(golden, produced)
    assert result.matches, result.render()


# ---------------------------------------------------------------------------
# 2. 配置写错时必须报错（fail-closed）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        # 手滑把 dimension 写成 dimensions —— 最危险的一种：不报错就测了别的东西
        ("未知键", {"metric": "贷款余额", "dimensinos": ["机构"]}),
        ("time 里的未知键", {"metric": "贷款余额", "time": {"grian": "month_end"}}),
        ("filters 里的未知键", {"metric": "x", "filters": [{"dimension": "机构", "vaule": "B001"}]}),
        ("grain 非法", {"metric": "贷款余额", "time": {"grain": "weekly"}}),
        ("日期非法", {"metric": "贷款余额", "time": {"grain": "month_end", "points": ["2026-02-30"]}}),
        ("points 写成字符串", {"metric": "贷款余额", "time": {"grain": "month_end", "points": "2026-04-30"}}),
        ("dimensions 写成字符串", {"metric": "贷款余额", "dimensions": "机构"}),
        ("filters 写成映射", {"metric": "x", "filters": {"dimension": "机构"}}),
        ("filter 缺 dimension", {"metric": "x", "filters": [{"value": "B001"}]}),
        ("operator 非法", {"metric": "x", "filters": [{"dimension": "机构", "operator": "like"}]}),
        ("source_mode 非法", {"metric": "x", "source_mode": "spark"}),
        ("comparison 非法", {"metric": "x", "comparison": "ytd"}),
    ],
)
def test_bad_mapping_raises(label: str, payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        intent_from_mapping(payload)


def test_time_is_not_inferred() -> None:
    """只给 grain 不给日期时不推算 —— 时间解析属于 L1/L2，不属于加载器。"""
    intent = intent_from_mapping({"metric": "贷款余额", "time": {"grain": "month_end"}})
    assert intent.time_range is not None
    assert intent.time_range.points == ()
    # 编译期会因为"没有具体时点"报 intent 类错误（见 compiler/time.py:56-66）
