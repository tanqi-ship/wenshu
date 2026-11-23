"""占比是独立业务意图，严格加载且保持旧输入兼容。"""

from dataclasses import fields

import pytest

from app.engine.semantic.models import Intent, ShareSpec, intent_from_mapping


def test_share_is_optional_and_keeps_existing_field_order():
    assert Intent().share is None
    assert intent_from_mapping({}).share is None
    assert intent_from_mapping({"share": None}).share is None
    assert [f.name for f in fields(Intent)][-3:] == ["confidence", "raw", "share"]


def test_share_mapping_keeps_business_dimension_not_sql():
    intent = intent_from_mapping(
        {"metric": "贷款余额", "dimensions": ["产品"], "share": {"dimension": " 产品 "}}
    )
    assert intent.share == ShareSpec("产品")
    assert intent.comparison is None


@pytest.mark.parametrize(
    "share",
    [
        {},
        {"dimension": ""},
        {"dimension": " "},
        {"dimension": None},
        {"dimension": True},
        {"dimension": 1},
        {"dimension": ["产品"]},
        {"dimension": "产品", "sql": "SELECT 1"},
        {"dimensions": ["产品"]},
        "产品",
        True,
        False,
        [],
        1,
    ],
)
def test_invalid_share_payload_fails_closed(share):
    with pytest.raises(ValueError):
        intent_from_mapping({"share": share})


def test_existing_rate_does_not_acquire_share_semantics():
    intent = intent_from_mapping({"comparison": "rate"})
    assert intent.comparison == "rate"
    assert intent.share is None
