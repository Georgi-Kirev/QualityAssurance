# TAG: TESTS - CROSS-SOURCE DEDUPLICATION
#
# Бъгът, който този файл държи:
#   calculate_fingerprint() хешира ЦЕЛИЯ запис, включително
#   source, dataset_id и източниковия id. Затова един и същ
#   реален имот от два различни източника получаваше РАЗЛИЧЕН
#   SHA-256 и никога не можеше да бъде премахнат като дубликат.
#   Освен това process_dataset() правеше нов seen_set за всеки
#   файл, така че и в рамките на един рън нямаше сравнение.
#
#   Резултат в реалния рън: 8242 входа -> 8242 изхода, 0 сливания.

import json

import pytest

from tools.deduplicator import (
    calculate_fingerprint,
    content_fingerprint,
    process_dataset,
)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [23.3301, 42.6962],
        [23.3402, 42.6962],
        [23.3402, 42.7010],
        [23.3301, 42.6962],
    ]],
}


def make_record(source, dataset_id, record_id, address, area):
    """Два източника, един и същ реален обект -> различни записи."""
    return {
        "source": source,
        "dataset_id": dataset_id,
        "attributes": {
            "id": record_id,
            "regname": address,
        },
        "location": {"geometry": GEOMETRY},
        "area": {"square_meters": area, "unit": "m2"},
        "price": {"value_eur": 59725.58, "currency": "EUR"},
    }


@pytest.fixture
def same_property_two_sources():
    return (
        make_record("sofiaplan", 624, "uuid-AAA", "ж.к. Люлин - 1 мр", 98225.38),
        make_record("otherportal", 17, "X-99", "ж.к. Люлин - 1 мр", 98225.38),
    )


@pytest.fixture
def different_properties():
    other_geometry = {
        "type": "Polygon",
        "coordinates": [[
            [23.4000, 42.6500],
            [23.4100, 42.6500],
            [23.4100, 42.6600],
            [23.4000, 42.6500],
        ]],
    }
    other = make_record("otherportal", 17, "X-77", "бул. Витоша 200", 1500.0)
    other["location"] = {"geometry": other_geometry}
    return other


# ============================================================
# TAG: WHY THE OLD FINGERPRINT FAILED
# ============================================================

def test_exact_fingerprint_cannot_see_cross_source_duplicates(
    same_property_two_sources
):
    """
    Документира ЗАЩО точният SHA-256 е структурно негоден.
    Ако този тест започне да минава различно, значи
    calculate_fingerprint() е променен - и трябва да се
    преразгледа дали още е нужен.
    """
    a, b = same_property_two_sources

    assert calculate_fingerprint(a) != calculate_fingerprint(b)


# ============================================================
# TAG: CONTENT FINGERPRINT
# ============================================================

def test_content_fingerprint_matches_same_property_across_sources(
    same_property_two_sources
):
    a, b = same_property_two_sources

    assert content_fingerprint(a) is not None
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_separates_different_properties(
    same_property_two_sources,
    different_properties
):
    a, _ = same_property_two_sources

    assert content_fingerprint(a) != content_fingerprint(different_properties)


def test_content_fingerprint_is_stable_within_same_source(
    same_property_two_sources
):
    a, _ = same_property_two_sources

    assert content_fingerprint(a) == content_fingerprint(a)


# ============================================================
# TAG: SAFETY RULES - NO FALSE MERGES
# ============================================================

def test_coordinates_only_is_never_a_duplicate():
    """
    Само координати не са достатъчни. Всички записи от един
    слой споделят геометрия и биха се сляли масово.
    Същият принцип като в matcher-а.
    """
    record = {
        "source": "x",
        "dataset_id": 1,
        "attributes": {"foo": 1},
        "location": {"geometry": GEOMETRY},
    }

    assert content_fingerprint(record) is None


def test_area_only_is_never_a_duplicate():
    record = {
        "source": "x",
        "dataset_id": 1,
        "attributes": {"foo": 1},
        "area": {"square_meters": 500.0},
    }

    assert content_fingerprint(record) is None


def test_empty_record_is_never_a_duplicate():
    assert content_fingerprint({"source": "x", "dataset_id": 1}) is None


def test_small_area_difference_still_same_object():
    """
    Два източника може да отдадат леко различна площ за един
    и същ обект. Отклонението от 1% трябва да ги запази
    като един и същ обект.
    """
    a = make_record("sofiaplan", 1, "a", "ул. Витоша 1", 1000.0)
    b = make_record("other", 2, "b", "ул. Витоша 1", 1009.0)

    assert content_fingerprint(a) == content_fingerprint(b)


def test_large_area_difference_is_different_object():
    a = make_record("sofiaplan", 1, "a", "ул. Витоша 1", 1000.0)
    b = make_record("other", 2, "b", "ул. Витоша 1", 4000.0)

    assert content_fingerprint(a) != content_fingerprint(b)


# ============================================================
# TAG: CROSS-FILE DEDUPLICATION (the actual bug)
# ============================================================

def _write(path, records):
    path.write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_dedup_across_separate_dataset_files(tmp_path):
    """
    РЕГРЕСИЯ: seen_set се създаваше ВЪТРЕ във process_dataset(),
    така че всеки файл беше изолиран свят. Един и същ обект,
    записан в два dataset файла от РАЗЛИЧНИ източници, минаваше
    и два пъти.
    """
    record_a = make_record("sofiaplan", 624, "uuid-AAA", "Люлин - 1", 98225.38)
    record_b = make_record("otherportal", 17, "X-99", "Люлин - 1", 98225.38)
    record_c = make_record("sofiaplan", 624, "uuid-CCC", "Люлин - 2", 81234.00)

    file_one = _write(tmp_path / "dataset_624.json", [record_a, record_c])
    file_two = _write(tmp_path / "dataset_17.json", [record_b])

    out_one = tmp_path / "out_624.json"
    out_two = tmp_path / "out_17.json"

    # Споделено content_sources -> вторият файл вижда първия.
    content_sources = {}

    first = process_dataset(
        file_one,
        out_one,
        content_sources=content_sources,
        source="sofiaplan",
    )
    second = process_dataset(
        file_two,
        out_two,
        content_sources=content_sources,
        source="otherportal",
    )

    assert first["output_records"] == 2
    assert first["duplicate_records"] == 0

    # Вторият файл: 1 запис, дубликат на вече изтрития.
    assert second["input_records"] == 1
    assert second["output_records"] == 0
    assert second["content_duplicates"] == 1


def test_year_series_is_not_a_duplicate(tmp_path):
    """
    РЕГРЕСИЯ - НАЙ-ВАЖНИЯТ ТЕСТ В ТОЗИ ФАЙЛ.

    dataset 624 (ikonomika.imoti_ceni_ge) съдържа ЦЕНА ПО
    ГОДИНА за всяка зона: 2 830 записа, само 148 различни
    зони, ~19 реда на зона. Всичките споделят ЕДНАКВА
    геометрия, ЕДНАКЪВ адрес и ЕДНАКВА площ.

    Първата версия на content dedup ги сля и dataset 624
    излезе с 0 записа - целият ценови слой изчезна от
    продукта, без грешка и без предупреждение.
    """
    same_zone = []

    for year in range(2002, 2021):
        record = make_record(
            "sofiaplan",
            624,
            f"uuid-{year}",
            "ж.к. Люлин - 1 мр",
            98225.38,
        )
        record["attributes"]["godina"] = year
        same_zone.append(record)

    path = _write(tmp_path / "dataset_624.json", same_zone)

    result = process_dataset(
        path,
        tmp_path / "out.json",
        source="sofiaplan",
    )

    # 19 години -> 19 реда. Нито един не бива да отпадне.
    assert result["input_records"] == 19
    assert result["output_records"] == 19
    assert result["duplicate_records"] == 0
    assert result["content_duplicates"] == 0


def test_two_sources_still_collapse_the_same_zone(tmp_path):
    """
    Обратната страна: същата зона от ДВА източника трябва да
    се слеят - това е точно за какво съществува дедупликацията.
    """
    from_sofiaplan = make_record(
        "sofiaplan", 624, "uuid-A", "ж.к. Люлин - 1 мр", 98225.38
    )
    from_portal = make_record(
        "imotportal", 9, "PID-7", "ж.к. Люлин - 1 мр", 98225.38
    )

    file_a = _write(tmp_path / "a.json", [from_sofiaplan])
    file_b = _write(tmp_path / "b.json", [from_portal])

    content_sources = {}

    first = process_dataset(
        file_a, tmp_path / "oa.json",
        content_sources=content_sources, source="sofiaplan",
    )
    second = process_dataset(
        file_b, tmp_path / "ob.json",
        content_sources=content_sources, source="imotportal",
    )

    assert first["output_records"] == 1
    assert second["input_records"] == 1
    assert second["output_records"] == 0
    assert second["content_duplicates"] == 1


def test_identical_records_within_one_source_are_removed(tmp_path):
    """
    Побайтови дубликати в един източник СЕ махат - за тях
    няма какво да се пази, те са буквално един и същ ред.
    """
    record = make_record("sofiaplan", 1, "a", "Люлин - 1", 1000.0)
    twin = json.loads(json.dumps(record))

    path = _write(tmp_path / "d.json", [record, twin, record])

    result = process_dataset(
        path, tmp_path / "o.json", source="sofiaplan"
    )

    assert result["input_records"] == 3
    assert result["output_records"] == 1
    assert result["exact_duplicates"] == 2
    assert result["duplicate_records"] == 2


def test_counts_records_without_identity_signals(tmp_path):
    """Записи без признаци се отчитат, но НЕ се сливат."""
    weak = [
        {"source": "x", "dataset_id": 1, "attributes": {"a": i}}
        for i in range(5)
    ]

    path = _write(tmp_path / "d.json", weak)
    result = process_dataset(path, tmp_path / "o.json")

    assert result["output_records"] == 5
    assert result["duplicate_records"] == 0
    assert result["unidentifiable_records"] == 5
