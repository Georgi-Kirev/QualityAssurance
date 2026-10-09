"""
Тестове за tools/consolidator.py

Покрива DSU clustering, extract_price (вкл. BGN конверсията)
и numeric integrity на изхода.
"""

import json

import pytest


# ============================================================
# TAG: DSU
# ============================================================

def test_dsu_union_and_find(consolidator):
    dsu = consolidator.DSU()

    dsu.union("a", "b")
    dsu.union("b", "c")

    assert dsu.find("a") == dsu.find("b")
    assert dsu.find("b") == dsu.find("c")


def test_dsu_keeps_separate_sets(consolidator):
    dsu = consolidator.DSU()

    dsu.union("a", "b")
    dsu.union("x", "y")

    assert dsu.find("a") != dsu.find("x")


def test_dsu_transitive_merge(consolidator):
    dsu = consolidator.DSU()

    dsu.union("1", "2")
    dsu.union("3", "4")
    dsu.union("2", "3")

    assert dsu.find("1") == dsu.find("4")


def test_dsu_single_element(consolidator):
    dsu = consolidator.DSU()

    dsu.union("solo", "solo")

    assert dsu.find("solo") == "solo"


# ============================================================
# TAG: extract_price
# ============================================================

def test_extract_price_from_block(consolidator):
    record = {
        "price": {
            "value_eur": 116813.08,
            "currency": "EUR"
        }
    }

    assert consolidator.extract_price(record) == pytest.approx(
        116813.08
    )


def test_extract_price_bgn_converted(consolidator):
    """
    РЕГРЕСИЯ: BGN стойност минаваше без конверсия и
    се маркираше като EUR (~2x твърде голяма).
    """

    record = {
        "attributes": {"price_bgn": 195583.0}
    }

    result = consolidator.extract_price(record)

    assert result == pytest.approx(100000.0, rel=1e-4)


def test_extract_price_eur_untouched(consolidator):
    record = {
        "attributes": {"price_eur": 100000.0}
    }

    assert consolidator.extract_price(record) == pytest.approx(
        100000.0
    )


def test_extract_price_none(consolidator):
    assert consolidator.extract_price({}) is None
    assert consolidator.extract_price(
        {"attributes": {}}
    ) is None


def test_extract_price_ignores_non_positive(consolidator):
    assert consolidator.extract_price(
        {"price": {"value_eur": 0}}
    ) is None
    assert consolidator.extract_price(
        {"price": {"value_eur": -5}}
    ) is None


def test_extract_price_accepts_string_number(consolidator):
    """
    Защита срещу Decimal/string корупцията - ако по някаква
    причина value_eur е string, extract_price все пак трябва
    да върне число, а не None.
    """

    record = {
        "price": {
            "value_eur": "116813.08"
        }
    }

    assert consolidator.extract_price(record) == pytest.approx(
        116813.08
    )


# ============================================================
# TAG: count_non_null
# ============================================================

def test_count_non_null(consolidator):
    record = {
        "a": 1,
        "b": None,
        "c": {"d": 2, "e": None},
        "f": [1, None, 3]
    }

    assert consolidator.count_non_null(record) == 4


def test_count_non_null_empty(consolidator):
    assert consolidator.count_non_null({}) == 0


# ============================================================
# TAG: RECORD UID
# ============================================================

def test_make_record_uid_roundtrip(consolidator):
    uid = consolidator.make_record_uid(
        "sofiaplan", "624", 17
    )

    assert consolidator.parse_record_uid(
        uid
    ) == ("sofiaplan", "624", 17)
