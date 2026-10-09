"""
Тестове за tools/finished_exporter.py

Покрива extract_area (с единици), price_history и
PERFORMANCE РЕГРЕСИЯТА с price_history индексите.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TIMEZONE = ZoneInfo("Europe/Sofia")


# ============================================================
# TAG: ИЗОЛАЦИЯ
# ============================================================

@pytest.fixture(autouse=True)
def isolated(isolated_storage):
    """
    run_export() пише в storage/finished/<timestamp>/.

    Без тази fixture файлът оставя реална снимка
    storage/finished/01-01-2026_10/ с тестово съдържание.
    Тя е ПОСЛЕДНАТА по име (01-01 е след 09-10), затова
    find_latest_finished_snapshot() я избира - и тя засенчва
    истинските данни на всеки следващ рън на dashboard-а.

    Същото е като мета-теста в test_no_network_in_suite.py,
    но от страната на storage/, не на storage_raw/.
    """

    yield isolated_storage


# ============================================================
# TAG: extract_area
# ============================================================

def test_extract_area_from_block(finished_exporter):
    record = {
        "area": {
            "square_meters": 3610.93,
            "unit": "m2"
        }
    }

    assert finished_exporter.extract_area(record) == pytest.approx(
        3610.93
    )


def test_extract_area_bulgarian_field(finished_exporter):
    record = {
        "attributes": {"area_kv_m": 3610.93}
    }

    assert finished_exporter.extract_area(record) == pytest.approx(
        3610.93
    )


def test_extract_area_unit_conversion(finished_exporter):
    """
    РЕГРЕСИЯ: max() без разглеждане на единиците.
    area_dka=500 трябва да е 500 000 m², не 500 m².
    """

    assert finished_exporter.extract_area({
        "attributes": {"area_dka": 500}
    }) == pytest.approx(500000.0)

    assert finished_exporter.extract_area({
        "attributes": {"area_ha": 2}
    }) == pytest.approx(20000.0)

    assert finished_exporter.extract_area({
        "attributes": {"area_ar": 50}
    }) == pytest.approx(5000.0)


def test_extract_area_mixed_units_picks_largest_in_m2(
    finished_exporter
):
    record = {
        "attributes": {
            "area_dka": 5,
            "area_kv_m": 80
        }
    }

    result = finished_exporter.extract_area(record)

    assert result == pytest.approx(5000.0), (
        "5 dka = 5000 m², 80 m² -> максимумът е 5000, "
        "НЕ 80 (както даваше старият max() без конверсия) "
        "и НЕ 500 (без конверсия от dka)"
    )


def test_extract_area_none(finished_exporter):
    assert finished_exporter.extract_area({}) is None
    assert finished_exporter.extract_area({
        "attributes": {"length_m": 19.09}
    }) is None


def test_extract_area_ignores_zero_and_negative(
    finished_exporter
):
    assert finished_exporter.extract_area({
        "area": {"square_meters": 0}
    }) is None
    assert finished_exporter.extract_area({
        "area": {"square_meters": -5}
    }) is None


def test_extract_area_top_level_area_m2(finished_exporter):
    assert finished_exporter.extract_area({
        "area_m2": 123.5
    }) == pytest.approx(123.5)


# ============================================================
# TAG: PRICE DELTA
# ============================================================

def test_compute_price_delta_increase(finished_exporter):
    assert finished_exporter.compute_price_delta(
        110.0, 100.0
    ) == pytest.approx(10.0)


def test_compute_price_delta_decrease(finished_exporter):
    assert finished_exporter.compute_price_delta(
        90.0, 100.0
    ) == pytest.approx(-10.0)


def test_compute_price_delta_unchanged(finished_exporter):
    assert finished_exporter.compute_price_delta(
        100.0, 100.0
    ) == pytest.approx(0.0)


def test_compute_price_delta_missing_values(finished_exporter):
    assert finished_exporter.compute_price_delta(None, 100.0) is None
    assert finished_exporter.compute_price_delta(100.0, None) is None
    assert finished_exporter.compute_price_delta(100.0, 0) is None


# ============================================================
# TAG: PROPERTY ID
# ============================================================

def test_property_id_is_stable(finished_exporter):
    record = {
        "source": "sofiaplan",
        "dataset_id": 624,
        "attributes": {"id": "abc-123", "regname": "Зона А"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    }

    first = finished_exporter.generate_property_id(record)
    second = finished_exporter.generate_property_id(dict(record))

    assert first == second
    assert first.startswith("prop_")


def test_property_id_differs_for_different_records(
    finished_exporter
):
    a = {
        "attributes": {"id": "abc-123"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    }

    b = {
        "attributes": {"id": "xyz-789"},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.4, 42.8]
            }
        }
    }

    assert finished_exporter.generate_property_id(
        a
    ) != finished_exporter.generate_property_id(b)


# ============================================================
# TAG: SNAPSHOT INDEX CACHE - PERFORMANCE РЕГРЕСИЯ
# ============================================================

def _write_snapshot(
    directory: Path,
    timestamp: str,
    records: list
):
    directory.mkdir(parents=True, exist_ok=True)

    (directory / "finished_properties.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    (directory / "metadata.json").write_text(
        json.dumps({"timestamp": timestamp}),
        encoding="utf-8"
    )

    return directory


def test_load_finished_index_is_cached(finished_exporter):
    """
    РЕГРЕСИЯ: load_finished_index() чеше 16 MB файла
    ЗА ВСЯКИ запис x всяко time-window. Без кеш експортът
    на 5 390 записа висеше над 30 минути.
    """

    finished_exporter._FINISHED_INDEX_CACHE.clear()

    snapshot = _write_snapshot(
        Path(finished_exporter.FINISHED_DIR) / "01-01-2026_10",
        "01-01-2026_10",
        [
            {"property_id": "prop_1", "price": 100000.0},
            {"property_id": "prop_2", "price": 250000.0}
        ]
    )

    first = finished_exporter.load_finished_index(snapshot)
    second = finished_exporter.load_finished_index(snapshot)

    assert first is second, (
        "вторият достъп трябва да върне кеширания обект, "
        "а не да чете файла отново"
    )

    assert len(first) == 2
    assert first["prop_1"][0] == pytest.approx(100000.0)

    finished_exporter._FINISHED_INDEX_CACHE.clear()


def test_select_historical_price_uses_preloaded_index(
    finished_exporter
):
    finished_exporter._FINISHED_INDEX_CACHE.clear()

    snapshot = _write_snapshot(
        Path(finished_exporter.FINISHED_DIR) / "01-01-2026_10",
        "01-01-2026_10",
        [{"property_id": "prop_1", "price": 100000.0}]
    )

    index = finished_exporter.load_finished_index(snapshot)

    current = datetime(2026, 1, 9, tzinfo=TIMEZONE)

    price = finished_exporter.select_historical_price(
        "prop_1",
        current,
        timedelta(days=6),
        timedelta(days=10),
        [(snapshot, index)]
    )

    assert price == pytest.approx(100000.0)

    # 100 дни назад - извън 1-месечния прозорец
    assert finished_exporter.select_historical_price(
        "prop_1",
        datetime(2026, 4, 20, tzinfo=TIMEZONE),
        timedelta(days=80),
        timedelta(days=100),
        [(snapshot, index)]
    ) is None

    finished_exporter._FINISHED_INDEX_CACHE.clear()


def test_select_historical_price_unknown_property(
    finished_exporter
):
    finished_exporter._FINISHED_INDEX_CACHE.clear()

    snapshot = _write_snapshot(
        Path(finished_exporter.FINISHED_DIR) / "01-01-2026_10",
        "01-01-2026_10",
        [{"property_id": "prop_1", "price": 100000.0}]
    )

    index = finished_exporter.load_finished_index(snapshot)

    price = finished_exporter.select_historical_price(
        "prop_missing",
        datetime(2026, 1, 12, tzinfo=TIMEZONE),
        timedelta(days=6),
        timedelta(days=10),
        [(snapshot, index)]
    )

    assert price is None

    finished_exporter._FINISHED_INDEX_CACHE.clear()


# ============================================================
# TAG: FULL EXPORT
# ============================================================

def test_run_export_writes_expected_output(
    finished_exporter,
    isolated_storage
):
    finished_exporter._FINISHED_INDEX_CACHE.clear()

    consolidated_dir = (
        isolated_storage / "consolidated" / "01-01-2026_10"
    )
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {
                "id": "z1",
                "regname": "Зона А",
                "cena_ap": 116813.079,
                "area_kv_m": 98225.38
            },
            "location": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[23.32, 42.69], [23.33, 42.70]]]
                }
            },
            "area": {
                "value": 98225.38,
                "unit": "m2",
                "square_meters": 98225.38
            },
            "price": {
                "value_eur": 116813.08,
                "currency": "EUR",
                "raw_value": 116813.079,
                "source_field": "cena_ap"
            }
        },
        {
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {
                "id": "z2",
                "regname": "Зона Б",
                "cena_ap": 95000.0,
                "area_kv_m": 54314.5
            },
            "location": {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[23.34, 42.71], [23.35, 42.72]]]
                }
            },
            "area": {
                "value": 54314.5,
                "unit": "m2",
                "square_meters": 54314.5
            },
            "price": {
                "value_eur": 95000.0,
                "currency": "EUR",
                "raw_value": 95000.0,
                "source_field": "cena_ap"
            }
        }
    ]

    (consolidated_dir / "consolidated.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    output = finished_exporter.run_export(
        consolidated_dir=consolidated_dir,
        auto_consolidate=False
    )

    assert output is not None
    assert output.exists()

    props_file = output / "finished_properties.json"

    assert props_file.exists()

    written = json.loads(
        props_file.read_text(encoding="utf-8")
    )

    assert len(written) == 2

    for record in written:
        assert record["price"] == pytest.approx(
            record["attributes"]["cena_ap"], abs=0.01
        )
        assert record["area_m2"] == pytest.approx(
            record["attributes"]["area_kv_m"]
        )
        assert "price_history" in record
        assert record["price_history"]["current_price"] is not None
        assert record["property_id"].startswith("prop_")

    finished_exporter._FINISHED_INDEX_CACHE.clear()


def test_run_export_sorted_by_price_ascending(
    finished_exporter,
    isolated_storage
):
    finished_exporter._FINISHED_INDEX_CACHE.clear()

    consolidated_dir = (
        isolated_storage / "consolidated" / "01-01-2026_11"
    )
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {
                "id": f"z{i}",
                "cena_ap": price,
                "area_kv_m": 1000.0
            },
            "location": {
                "geometry": {
                    "type": "Point",
                    "coordinates": [23.3 + i / 100, 42.7]
                }
            },
            "price": {
                "value_eur": price,
                "currency": "EUR",
                "raw_value": price,
                "source_field": "cena_ap"
            }
        }
        for i, price in enumerate([300000.0, 100000.0, 200000.0])
    ]

    (consolidated_dir / "consolidated.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    output = finished_exporter.run_export(
        consolidated_dir=consolidated_dir,
        auto_consolidate=False
    )

    written = json.loads(
        (output / "finished_properties.json").read_text(
            encoding="utf-8"
        )
    )

    prices = [r["price"] for r in written]

    assert prices == sorted(prices)
    assert prices[0] == pytest.approx(100000.0)

    finished_exporter._FINISHED_INDEX_CACHE.clear()


def test_run_export_no_consolidated_returns_none(
    finished_exporter,
    isolated_storage
):
    assert finished_exporter.run_export(
        consolidated_dir=isolated_storage / "consolidated" / "missing",
        auto_consolidate=False
    ) is None
