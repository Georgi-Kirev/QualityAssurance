"""
Тестове за tools/deduplicator.py

Проверява fingerprint логиката и РЕГРЕСИЯ срещу
Decimal→string корупцията (ijson use_float).
"""

import json
from pathlib import Path

import pytest


# ============================================================
# TAG: FINGERPRINT
# ============================================================

def test_fingerprint_is_deterministic(deduplicator):
    record = {
        "source": "sofiaplan",
        "dataset_id": 1,
        "attributes": {"a": 1, "b": "x"}
    }

    first = deduplicator.calculate_fingerprint(record)
    second = deduplicator.calculate_fingerprint(dict(record))

    assert first == second
    assert len(first) == 64


def test_fingerprint_ignores_key_order(deduplicator):
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}

    assert deduplicator.calculate_fingerprint(
        a
    ) == deduplicator.calculate_fingerprint(b)


def test_fingerprint_differs_on_value(deduplicator):
    a = {"a": 1}
    b = {"a": 2}

    assert deduplicator.calculate_fingerprint(
        a
    ) != deduplicator.calculate_fingerprint(b)


def test_canonical_json_is_bytes(deduplicator):
    result = deduplicator.canonical_json({"a": 1})

    assert isinstance(result, bytes)
    assert b'"a":1' in result


# ============================================================
# TAG: NUMERIC INTEGRITY - РЕГРЕСИЯ
# ============================================================

def test_read_normalized_records_returns_floats(
    deduplicator,
    tmp_path
):
    """
    РЕГРЕСИЯ: ijson без use_float=True връща Decimal.
    След json.dumps(default=str) всички числа ставаха
    string и extract_price() връщаше None.
    """

    from decimal import Decimal

    path = tmp_path / "dataset_1.json"
    path.write_text(
        '[{"price":{"value_eur":116813.08,'
        '"raw_value":116813.079},"area_m2":3610.93}]',
        encoding="utf-8"
    )

    records = list(
        deduplicator.read_normalized_records(path)
    )

    assert len(records) == 1

    record = records[0]

    assert isinstance(
        record["price"]["value_eur"], float
    )
    assert not isinstance(
        record["price"]["value_eur"], Decimal
    )
    assert not isinstance(
        record["price"]["value_eur"], str
    )
    assert isinstance(record["area_m2"], float)


def test_process_dataset_preserves_numbers(
    deduplicator,
    tmp_path
):
    """
    Пълният pipeline на deduplicator-а трябва да запази
    числата като числа в изходния файл.
    """

    source = tmp_path / "dataset_1.json"
    target = tmp_path / "out" / "dataset_1.json"

    source.write_text(
        json.dumps(
            [
                {
                    "price": {
                        "value_eur": 116813.08,
                        "raw_value": 116813.079
                    },
                    "area_m2": 3610.93,
                    "godina": 2019
                }
            ],
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    stats = deduplicator.process_dataset(
        source, target
    )

    assert stats["output_records"] == 1
    assert target.exists()

    written = json.loads(
        target.read_text(encoding="utf-8")
    )

    assert isinstance(
        written[0]["price"]["value_eur"], float
    )
    assert not isinstance(
        written[0]["price"]["value_eur"], str
    )
    assert isinstance(written[0]["godina"], int)
    assert not isinstance(written[0]["godina"], str)


# ============================================================
# TAG: DUPLICATE REMOVAL
# ============================================================

def test_exact_duplicates_removed(
    deduplicator,
    tmp_path
):
    source = tmp_path / "dataset_1.json"
    target = tmp_path / "out" / "dataset_1.json"

    record = {
        "source": "sofiaplan",
        "attributes": {"a": 1}
    }

    source.write_text(
        json.dumps(
            [record, dict(record), {"attributes": {"a": 2}}],
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    stats = deduplicator.process_dataset(
        source, target
    )

    assert stats["input_records"] == 3
    assert stats["output_records"] == 2
    assert stats["duplicate_records"] == 1

    written = json.loads(
        target.read_text(encoding="utf-8")
    )

    assert len(written) == 2


def test_empty_input(deduplicator, tmp_path):
    source = tmp_path / "dataset_1.json"
    target = tmp_path / "out" / "dataset_1.json"

    source.write_text("[]", encoding="utf-8")

    stats = deduplicator.process_dataset(
        source, target
    )

    assert stats["input_records"] == 0
    assert stats["output_records"] == 0
    assert target.read_text(encoding="utf-8") == "[]"


def test_output_is_atomic_no_tmp_left(
    deduplicator,
    tmp_path
):
    source = tmp_path / "dataset_1.json"
    target = tmp_path / "out" / "dataset_1.json"

    source.write_text(
        '[{"a":1}]', encoding="utf-8"
    )

    deduplicator.process_dataset(source, target)

    assert not list(
        target.parent.glob("*.tmp")
    )


# ============================================================
# TAG: INCOMPLETE RUNS ARE IGNORED
# ============================================================

def test_incomplete_normalized_run_is_ignored(
    deduplicator,
    tmp_path,
    monkeypatch,
):
    """
    Прекъснат normalizer run (без metadata.json) трябва да
    бъде пропуснат от deduplicator-а, дори да е най-новият.

    Без това dedup би работил с непълни данни.
    """

    base = tmp_path / "normalized"

    complete = base / "01-01-2026_10"
    complete.mkdir(parents=True)
    (complete / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    (complete / "sofiaplan").mkdir()
    (complete / "sofiaplan" / "dataset_1.json").write_text(
        "[]", encoding="utf-8"
    )

    # По-нов, но НЕЗАВЪРШЕН run.
    incomplete = base / "01-01-2026_11"
    incomplete.mkdir(parents=True)
    (incomplete / "sofiaplan").mkdir()
    (incomplete / "sofiaplan" / "dataset_2.json").write_text(
        "[]", encoding="utf-8"
    )

    monkeypatch.setattr(
        deduplicator, "NORMALIZED_DIR", base
    )

    snapshots = deduplicator.find_normalized_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].name == "01-01-2026_10"

    latest = deduplicator.find_latest_normalized_snapshot()

    assert latest.name == "01-01-2026_10"
