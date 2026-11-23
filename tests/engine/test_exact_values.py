"""严格值登记独立于 SQL；普通 coded 保留旧契约。"""

from dataclasses import replace

import pytest

from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef, LevelDef
from app.engine.semantic.exact_values import ExactValueError


def catalog(mapping=None, *, kind="high_cardinality", supplied=True):
    return SemanticCatalog.build(
        dimensions=[DimensionDef("org", "机构", {"default": LevelDef("org_no")}, value_kind=kind)],
        code_map={"org": mapping} if supplied else {},
    )


@pytest.mark.parametrize("value", ["渝北支行", "渝北", "B001", "  渝北  ", " B001 "])
def test_exact_registered_tokens(value):
    assert catalog({"渝北支行": "B001", "渝北": "B001"}).code("org", value) == "B001"


@pytest.mark.parametrize(
    "value", ["渝 北", "b001", "B0019", "渝北%", "", "  ", 1, True, None, [], {}, ["渝北"]]
)
def test_unknown_or_non_string_never_guessed(value):
    with pytest.raises(ExactValueError) as error:
        catalog({"渝北": "B001"}).code("org", value)
    assert error.value.reason == "unknown"


def test_same_trimmed_alias_same_code_is_not_ambiguous():
    assert catalog({"渝北": "B001", " 渝北 ": "B001"}).code("org", "渝北") == "B001"


@pytest.mark.parametrize(
    "mapping",
    [
        {"渝北": "B001", " 渝北 ": "B002"},
        {"B001": "B002", "渝北": "B001"},
    ],
)
def test_build_rejects_all_ambiguity_including_key_code_collision(mapping):
    with pytest.raises(ExactValueError) as error:
        catalog(mapping)
    assert error.value.reason == "ambiguous"


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        [],
        "bad",
        {1: "B001"},
        {"": "B001"},
        {" ": "B001"},
        {"渝北": ""},
        {"渝北": " B001"},
        {"渝北": "B001 "},
        {"渝北": 1},
    ],
)
def test_build_rejects_invalid_mapping(mapping):
    with pytest.raises(ValueError):
        catalog(mapping)


@pytest.mark.parametrize("mapping,supplied", [(None, False), ({}, True)])
def test_missing_and_empty_map_can_load_but_cannot_resolve(mapping, supplied):
    item = catalog(mapping, supplied=supplied)
    with pytest.raises(ExactValueError) as error:
        item.code("org", "B001")
    assert error.value.reason == "missing"


@pytest.mark.parametrize(
    "mapping,reason",
    [({}, "missing"), ({"渝北": " B001"}, "invalid"), ({"B001": "B002", "渝北": "B001"}, "ambiguous")],
)
def test_direct_replace_cannot_bypass_validation(mapping, reason):
    item = replace(catalog({"渝北": "B001"}), code_map={"org": mapping})
    with pytest.raises(ExactValueError) as error:
        item.code("org", "B001")
    assert error.value.reason == reason


@pytest.mark.parametrize("kind", ["unknown", "", None, [], 123])
def test_build_rejects_invalid_value_kind(kind):
    with pytest.raises(ValueError):
        catalog({}, kind=kind)


def test_direct_replace_invalid_kind_rejected_on_resolution():
    item = catalog({"渝北": "B001"})
    dim = replace(item.dimensions["org"], value_kind="bad")
    with pytest.raises(ExactValueError) as error:
        replace(item, dimensions={"org": dim}).code("org", "B001")
    assert error.value.reason == "invalid"


def test_coded_normalization_and_missing_map_contract_unchanged():
    assert catalog({"A B": "X"}, kind="coded").code("org", "ab") == "X"
    assert catalog({}, kind="coded").code("org", "B001") is None


def test_strict_values_do_not_unicode_fold_and_allow_literal_wildcards():
    item = catalog({"Ａ": "X", "A%_机构": "Y"})
    assert item.code("org", "Ａ") == "X"
    assert item.code("org", "A%_机构") == "Y"
    with pytest.raises(ExactValueError):
        item.code("org", "A")


@pytest.mark.parametrize("mapping", [[], "bad", 1])
def test_invalid_root_mapping_is_rejected_at_build_and_direct_resolution(mapping):
    item = catalog({"渝北": "B001"})
    with pytest.raises(ValueError):
        SemanticCatalog.build(dimensions=item.dimensions.values(), code_map=mapping)
    with pytest.raises(ExactValueError) as error:
        replace(item, code_map=mapping).code("org", "B001")
    assert error.value.reason == "invalid"
