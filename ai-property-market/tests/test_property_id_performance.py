# TAG: TESTS - PROPERTY ID PERFORMANCE
#
# Бъгът, който този файл държи:
#   generate_property_id() изчисляваше координатен подпис чрез
#   обхождане на ВСИЧКИ точки в геометрията и после сортираше
#   (min). Реалните записи от SofiaPlan носят средно 15 332
#   координати (~457 KB) всеки.
#
#   Измерено: 13.7 ms/record = 73 записа/сек.
#   При 1 781 797 записа това е около 7 ЧАСА за един етап,
#   при 0.17 GB RAM - т.е. не е въпрос на ресурс, а на
#   алгоритъм.
#
#   count_non_null() също обхождаше цялата геометрия (9 ms),
#   макар че "богатство" на записа не бива да зависи от броя
#   на върховете на.polygon-а.

import time

import pytest

from tools.consolidator import count_non_null
from tools.finished_exporter import (
    extract_coordinate_signature,
    generate_property_id,
)


def _big_polygon(points: int = 15000):
    """
    Полигон с много върхове - реалният случай.
    """
    coords = []
    for i in range(points):
        lon = 23.30 + (i % 100) * 1e-5
        lat = 42.69 + (i // 100) * 1e-5
        coords.append([lon, lat])
    coords.append(coords[0])
    return {
        "location": {
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords],
            }
        }
    }


@pytest.fixture
def big_record():
    return {
        "source": "sofiaplan",
        "dataset_id": 619,
        "attributes": {
            "id": "abc-123",
            "regname": "ж.к. Люлин - 1 мр",
            "plosht": 98225.38,
        },
        **_big_polygon(),
        "area": {"square_meters": 98225.38, "unit": "m2"},
    }


# ============================================================
# TAG: SPEED
# ============================================================

def test_property_id_is_fast_on_huge_geometry(big_record):
    """
    Прагът е 2 ms. Преди поправката този запис отнемаше ~14 ms.
    """
    started = time.perf_counter()
    generate_property_id(big_record)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 2.0, (
        f"generate_property_id() took {elapsed_ms:.1f} ms on a "
        f"15 000-point polygon. It must not walk the geometry."
    )


def test_count_non_null_is_fast_on_huge_geometry(big_record):
    started = time.perf_counter()
    count_non_null(big_record)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 2.0, (
        f"count_non_null() took {elapsed_ms:.1f} ms. "
        f"Geometry must count as one fact, not 15 000 values."
    )


def test_coordinate_signature_is_fast(big_record):
    started = time.perf_counter()
    extract_coordinate_signature(big_record)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 2.0


# ============================================================
# TAG: CORRECTNESS IS PRESERVED
# ============================================================

def test_property_id_is_stable_across_calls(big_record):
    """
    Стабилността е условие за price_history да работи.
    """
    first = generate_property_id(big_record)
    second = generate_property_id(big_record)

    assert first == second


def test_property_id_differs_for_different_places():
    a = {
        "attributes": {"regname": "Люлин - 1"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.30, 42.69],
            }
        },
    }
    b = {
        "attributes": {"regname": "Люлин - 1"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.90, 42.19],
            }
        },
    }

    assert generate_property_id(a) != generate_property_id(b)


def test_property_id_differs_for_different_addresses():
    # "address" е в ADDRESS_KEYS; "regname" (SofiaPlan) не е и
    # умишлено не се ползва тук.
    a = {
        "attributes": {"address": "ул. Витоша 1"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.30, 42.69],
            }
        },
    }
    b = {
        "attributes": {"address": "ул. Витоша 2"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.30, 42.69],
            }
        },
    }

    assert generate_property_id(a) != generate_property_id(b)


def test_point_geometry_signature():
    record = {
        "location": {
            "geometry": {"type": "Point", "coordinates": [23.5, 42.7]}
        }
    }

    assert extract_coordinate_signature(record) == "42.7000,23.5000"


def test_polygon_first_vertex_signature():
    record = {
        "location": {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[23.10, 42.20], [23.11, 42.21]]],
            }
        }
    }

    assert extract_coordinate_signature(record) == "42.2000,23.1000"


def test_multipolygon_signature():
    record = {
        "location": {
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[23.10, 42.20], [23.11, 42.21]]],
                    [[[24.10, 43.20], [24.11, 43.21]]],
                ],
            }
        }
    }

    # Първият връх на първия полигон, не последният.
    assert extract_coordinate_signature(record) == "42.2000,23.1000"


def test_missing_geometry_is_empty_signature():
    assert extract_coordinate_signature({}) == ""
    assert extract_coordinate_signature({"location": {}}) == ""
    assert extract_coordinate_signature(
        {"location": {"geometry": {}}}
    ) == ""


# ============================================================
# TAG: RICHNESS SEMANTICS
# ============================================================

def test_geometry_counts_as_one_fact():
    small = {
        "attributes": {"a": 1},
        "location": {
            "geometry": {"type": "Point", "coordinates": [23.3, 42.7]}
        },
    }
    huge = {
        "attributes": {"a": 1},
        **_big_polygon(15000),
    }

    assert count_non_null(huge) == count_non_null(small), (
        "броят на върховете не бива да влияе на 'богатството'"
    )


def test_richness_still_counts_real_attributes():
    a = {"attributes": {"a": 1}}
    b = {"attributes": {"a": 1, "b": 2, "c": 3}}

    assert count_non_null(b) > count_non_null(a)


def test_empty_geometry_is_zero():
    assert count_non_null(
        {"location": {"geometry": None}}
    ) == 0
