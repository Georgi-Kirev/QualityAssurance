# TAG: TESTS - AREA SPLIT
#
# Имотът има две различни площи:
#   * цялостна (удебел) - площта на парцела като цяло
#   * застроена         - реално построената част
#
# Ако се запише само едно число, агентът не може да прецени
# колко от земята е използваемо. Затова изходът носи и двете.

import pytest

from tools.finished_exporter import (
    AREA_BUILT_KEYS,
    AREA_TOTAL_KEYS,
    _classify_area_key,
    split_area,
)


# ============================================================
# TAG: FIELD NAME CLASSIFICATION
# ============================================================

@pytest.mark.parametrize("key", [
    "zastroena_plosht",
    "zas_izgradena",
    "built_area",
    "built_up_area",
    "footprint",
    "застроена_площ",
])
def test_built_area_names_are_recognised(key):
    assert _classify_area_key(key) == "built"


@pytest.mark.parametrize("key", [
    "plosht",
    "parcela",
    "plot_area",
    "dvorasht",
    "uma",
    "имота",
])
def test_total_area_names_are_recognised(key):
    assert _classify_area_key(key) == "total"


def test_built_wins_over_total_in_same_name():
    """
    'zastroena_uma' съдържа и двата маркера. Застроената е
    по-конкретната информация и трябва да победи.
    """
    assert _classify_area_key("zastroena_uma") == "built"


def test_unknown_name_is_neither():
    assert _classify_area_key("some_random_field") == ""


# ============================================================
# TAG: SPLIT BEHAVIOUR
# ============================================================

def test_splits_built_and_total():
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {
            "plosht_uma": 1200.0,
            "zastroena_plosht": 180.0,
        },
        "area": {"square_meters": 1200.0},
    }

    total, built = split_area(record)

    assert total == pytest.approx(1200.0)
    assert built == pytest.approx(180.0)


def test_total_only_leaves_built_none():
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {"plosht": 900.0},
        "area": {"square_meters": 900.0},
    }

    total, built = split_area(record)

    assert total == pytest.approx(900.0)
    assert built is None


def test_built_only_still_reports_total():
    """
    Има само застроена площ. Тя НЕ е цялостната, затова
    built се връща, а total пада към общия блок (ако има).
    """
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {"zastroena": 150.0},
        "area": {"square_meters": 800.0},
    }

    total, built = split_area(record)

    assert built == pytest.approx(150.0)
    assert total == pytest.approx(800.0)


def test_never_invents_built_area():
    """
    КЛЮЧОВО: липсваща застроена площ остава None.
    По-добре е липсваща стойност, отколкото измислена.
    """
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {"plosht": 500.0},
        "area": {"square_meters": 500.0},
    }

    _total, built = split_area(record)

    assert built is None


def test_built_cannot_exceed_total():
    """
    Противоречиви данни: застроена > цялостна. Втората е
    отхвърлена, защото иначе агентът получава безсмислена
    стойност.
    """
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {
            "plosht": 100.0,
            "zastroena": 500.0,
        },
        "area": {"square_meters": 100.0},
    }

    total, built = split_area(record)

    assert total == pytest.approx(100.0)
    assert built is None


def test_area_block_used_when_no_attributes():
    record = {
        "source": "sofiaplan",
        "dataset_id": 624,
        "attributes": {"cena_ap": 11722.96},
        "area": {"square_meters": 543145.06, "unit": "m2"},
    }

    total, built = split_area(record)

    assert total == pytest.approx(543145.06)
    assert built is None


def test_record_without_area_returns_nones():
    record = {
        "source": "x",
        "dataset_id": 1,
        "attributes": {"foo": "bar"},
    }

    total, built = split_area(record)

    assert total is None
    assert built is None


def test_ambiguous_field_name_is_not_built():
    """
    Поле без маркер за застроена площ (напр. 'plosht')
    не трябва да се третира като застроена.
    """
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {"plosht": 640.0},
        "area": {"square_meters": 640.0},
    }

    _total, built = split_area(record)

    assert built is None


def test_decares_are_converted_to_square_meters():
    """Парцел от 2 декара = 2000 m²."""
    record = {
        "source": "portal",
        "dataset_id": 1,
        "attributes": {
            "plosht_decara": 2.0,
            "zastroena_plosht": 140.0,
        },
    }

    total, built = split_area(record)

    assert total == pytest.approx(2000.0, rel=1e-6)
    assert built == pytest.approx(140.0)
