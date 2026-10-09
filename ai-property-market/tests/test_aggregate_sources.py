"""
Тестове за изключването на агрегатните източници.

# TAG: ЗАЩО ТОЗИ ФАЙЛ СЪЩЕСТВУВА

Открито емпирично на 28.09.2026: NSI връща 1241 записа
от HPI_2.1-en.xlsx. Всичките са АГРЕГАТНИ ИНДЕКСИ
(2015=100, 2025=100), а не имотни обяви.

Проверено с реален код, не на око:

  - normalize_record() връща запис за всеки ред
  - finished_exporter.generate_property_id() дава
    1241 РАЗЛИЧНИ property_id-та
  - extract_address_signature() е ПРАЗЕН за 1241/1241

Тоест без изрично изключване пайплайнът би публикувал
1241 фантомни "имота" без адрес, без локация, без площ и
без цена, а metadata.json би показвал грешен брой.

Тестовете тук пазят точно това поведение. Ако някой
премахне изключването, тези тестове падат.
"""

import json

import pytest

from tools import normalizer


# ============================================================
# TAG: CONFIGURATION
# ============================================================

def test_nsi_is_excluded():
    assert "nsi" in normalizer.NON_PROPERTY_SOURCES


def test_egov_is_excluded():
    """
    eGov каталогът е СПИСЪК на набори, не имотни обяви.
    """
    assert "egov" in normalizer.NON_PROPERTY_SOURCES


def test_sofiaplan_is_not_excluded():
    """
    Регресия: не бива да се изключи основният източник.
    """
    assert (
        "sofiaplan"
        not in normalizer.NON_PROPERTY_SOURCES
    )


# ============================================================
# TAG: DISCOVERY
# ============================================================

def _make_dataset(root, source, dataset_id="900001"):
    directory = (
        root / source / "28-09-2026_10" / "datasets"
        / str(dataset_id)
    )
    directory.mkdir(parents=True, exist_ok=True)
    payload = directory / "raw_data.json"
    payload.write_text("[]", encoding="utf-8")
    return payload


def test_nsi_snapshot_is_not_discovered(tmp_path, monkeypatch):
    _make_dataset(tmp_path, "nsi")
    monkeypatch.setattr(
        normalizer, "RAW_DIR", tmp_path
    )

    snapshots = normalizer.discover_raw_snapshots()

    assert snapshots == []


def test_sofiaplan_is_still_discovered(
    tmp_path, monkeypatch
):
    _make_dataset(tmp_path, "sofiaplan")
    monkeypatch.setattr(
        normalizer, "RAW_DIR", tmp_path
    )

    snapshots = normalizer.discover_raw_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0]["source"] == "sofiaplan"


def test_property_source_survives_alongside_nsi(
    tmp_path, monkeypatch
):
    """
    Изключването не бива да прекъсва и останалите.
    """
    _make_dataset(tmp_path, "nsi")
    _make_dataset(tmp_path, "sofiaplan")

    monkeypatch.setattr(
        normalizer, "RAW_DIR", tmp_path
    )

    sources = {
        s["source"]
        for s in normalizer.discover_raw_snapshots()
    }

    assert sources == {"sofiaplan"}


def test_egov_is_not_discovered(tmp_path, monkeypatch):
    _make_dataset(tmp_path, "egov")
    monkeypatch.setattr(
        normalizer, "RAW_DIR", tmp_path
    )

    assert normalizer.discover_raw_snapshots() == []


# ============================================================
# TAG: THE REAL DATA
# ============================================================

REAL_AGGREGATE_PATH = (
    normalizer.BASE_DIR
    / "storage_raw" / "nsi" / "reference"
    / "28-09-2026_HPI"
    / "aggregate_dataset_HPI_2.1.json"
)


@pytest.mark.skipif(
    not REAL_AGGREGATE_PATH.exists(),
    reason="Real NSI aggregate dataset not present",
)
def test_real_nsi_records_carry_no_property_fields():
    """
    Реалните 1241 записа не бива да имат адрес или цена.
    Ако някой ден започват да имат, те вече не са
    агрегати и изключването трябва да се преразгледа.
    """
    records = json.loads(
        REAL_AGGREGATE_PATH.read_text(encoding="utf-8")
    )

    assert len(records) == 1241

    for record in records:
        assert record["record_kind"] == "aggregate"
        attributes = record["attributes"]
        assert "period" in attributes
        assert "geography" in attributes
        assert "value" in attributes
        # Ключово: няма имотни полета.
        assert "address" not in record
        assert "location" not in record
        assert "price" not in record
        assert "area" not in record


@pytest.mark.skipif(
    not REAL_AGGREGATE_PATH.exists(),
    reason="Real NSI aggregate dataset not present",
)
def test_real_nsi_would_have_inflated_the_count():
    """
    Документира точно колко щеше да потече числото,
    ако изключването го нямаше. Стойността е ЗАПИСАНА
    нарочно - ако следващ път данните станат 1000 или
    20 000, това е промяна, която трябва да се види.
    """
    records = json.loads(
        REAL_AGGREGATE_PATH.read_text(encoding="utf-8")
    )

    assert len(records) == 1241
