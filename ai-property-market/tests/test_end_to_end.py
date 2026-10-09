"""
End-to-end тестове.

Пуска ЦЕЛИЯ pipeline (normalizer -> deduplicator ->
consolidator -> finished_exporter) върху малък
синтетичен dataset, без мрежа.

Това е тестът, който би хванал повечето от регрессиите,
описани в PROGRESS.md.
"""

import json
from pathlib import Path

import pytest


# ============================================================
# TAG: PIPELINE
# ============================================================

def test_full_pipeline_end_to_end(
    tmp_path,
    monkeypatch,
    sample_features
):
    """
    Целият pipeline върху 6 записа.
    """

    from tools import normalizer as norm
    from tools import deduplicator as dedup
    from tools import consolidator as cons
    from tools import finished_exporter as fin

    storage = tmp_path / "storage"
    raw = tmp_path / "storage_raw"
    storage.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # RAW
    # --------------------------------------------------------

    ts = "01-01-2026_10"

    dataset_dir = (
        raw / "sofiaplan" / ts / "datasets" / "999"
    )
    dataset_dir.mkdir(parents=True, exist_ok=True)

    geojson_path = dataset_dir / "raw_data.geojson"

    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": sample_features
            },
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # ISOLATE ALL STORAGE
    # --------------------------------------------------------

    normalized_dir = storage / "normalized"
    deduplicated_dir = storage / "deduplicated"
    consolidated_dir = storage / "consolidated"
    finished_dir = storage / "finished"

    monkeypatch.setattr(
        norm, "RAW_DIR", raw
    )
    monkeypatch.setattr(
        norm, "NORMALIZED_DIR", normalized_dir
    )
    monkeypatch.setattr(
        dedup, "NORMALIZED_DIR", normalized_dir
    )
    monkeypatch.setattr(
        dedup, "DEDUPLICATED_DIR", deduplicated_dir
    )
    monkeypatch.setattr(
        cons, "DEDUPLICATED_DIR", deduplicated_dir
    )
    monkeypatch.setattr(
        cons, "CONSOLIDATED_DIR", consolidated_dir
    )
    monkeypatch.setattr(
        cons, "INDEX_DIR", storage / "indexes"
    )
    monkeypatch.setattr(
        cons, "MATCHED_DIR", storage / "matched"
    )
    monkeypatch.setattr(
        fin, "FINISHED_DIR", finished_dir
    )
    monkeypatch.setattr(
        fin, "ANALYTICS_DIR", storage / "analytics"
    )

    fin._FINISHED_INDEX_CACHE.clear()

    # --------------------------------------------------------
    # STEP 1: NORMALIZE
    # --------------------------------------------------------

    snapshots = norm.discover_raw_snapshots()

    assert len(snapshots) == 1, (
        f"очакваше 1 raw snapshot, получи {len(snapshots)}"
    )

    jobs = norm.build_unique_dataset_jobs(snapshots)

    assert len(jobs) == 1, f"очакваше 1 dataset, получи {len(jobs)}"

    job = jobs[0]

    source = job["source"]
    dataset_id = str(job["dataset_id"])

    norm_output = normalized_dir / ts

    normalized_dir.mkdir(
        parents=True, exist_ok=True
    )

    result = norm.normalize_dataset_once(
        job, norm_output
    )

    assert result is not None

    normalized_file = (
        norm_output / source / f"dataset_{dataset_id}.json"
    )

    assert normalized_file.exists()

    normalized = json.loads(
        normalized_file.read_text(encoding="utf-8")
    )

    assert len(normalized) == len(sample_features)

    # числата трябва да са числа
    for record in normalized:
        if "cena_ap" in record["attributes"]:
            assert isinstance(
                record["attributes"]["cena_ap"], float
            ), "цената не бива да е string"

    # --------------------------------------------------------
    # MARKER: завършен run
    # --------------------------------------------------------
    #
    # Тук извикваме normalize_dataset_once() директно, а не
    # norm.main(), затова metadata.json липсва. В реалния
    # pipeline main() го пише. Без него dedup правилно
    # счита snapshot-а за незавършен и го пропуска.

    (norm_output / "metadata.json").write_text(
        json.dumps(
            {
                "normalizer_version": "4.4",
                "run_timestamp": ts,
                "datasets_found": 1,
                "successful": 1,
                "failed": 0,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # STEP 2: DEDUPLICATE
    # --------------------------------------------------------

    rc = dedup.run_deduplication()

    assert rc == 0, "deduplicator-ът върна код за грешка"
    dedup_snapshot = _latest(
        deduplicated_dir
    )

    assert dedup_snapshot is not None

    dedup_file = (
        dedup_snapshot / source
        / f"dataset_{dataset_id}.json"
    )

    assert dedup_file.exists()

    deduplicated = json.loads(
        dedup_file.read_text(encoding="utf-8")
    )

    assert len(deduplicated) == len(sample_features)

    for record in deduplicated:
        if "cena_ap" in record["attributes"]:
            assert isinstance(
                record["attributes"]["cena_ap"], float
            ), (
                "РЕГРЕСИЯ: deduplicator-ът превърна числото "
                "в string"
            )

    # --------------------------------------------------------
    # STEP 3: CONSOLIDATE
    # --------------------------------------------------------

    consolidated = cons.run_consolidation(
        snapshot_dir=dedup_snapshot,
        output_dir=consolidated_dir / ts,
        use_existing_matches=False
    )

    assert consolidated is not None

    consolidated_file = (
        consolidated / "consolidated.json"
    )

    assert consolidated_file.exists()

    clusters = json.loads(
        consolidated_file.read_text(encoding="utf-8")
    )

    # 6-те записа са 6 различни обекта (зони/реки/парк)
    # без общ идентификатор -> нито един не бива да бъде
    # слят с друг.
    assert len(clusters) == len(sample_features), (
        f"очакваше {len(sample_features)} клъстера, "
        f"получи {len(clusters)}"
    )

    # --------------------------------------------------------
    # STEP 4: EXPORT FINISHED
    # --------------------------------------------------------

    output = fin.run_export(
        consolidated_dir=consolidated,
        auto_consolidate=False
    )

    assert output is not None

    props_file = output / "finished_properties.json"

    assert props_file.exists()

    finished = json.loads(
        props_file.read_text(encoding="utf-8")
    )

    assert len(finished) == len(sample_features)

    with_price = [
        r for r in finished
        if r.get("price") is not None
    ]

    with_area = [
        r for r in finished
        if r.get("area_m2") is not None
    ]

    # 3 от 6 записа имат cena_ap (зоните)
    assert len(with_price) == 3, (
        f"очакваше 3 записа с цена, "
        f"получи {len(with_price)}"
    )

    # 4 от 6 имат площ (3 зони + 1 парк)
    assert len(with_area) == 4, (
        f"очакваше 4 записа с площ, "
        f"получи {len(with_area)}"
    )

    for record in with_price:
        assert isinstance(
            record["price"], (int, float)
        )
        assert record["price"] > 0

    for record in with_area:
        assert isinstance(
            record["area_m2"], (int, float)
        )
        assert record["area_m2"] > 0

    # price_history трябва да е структурирано
    for record in finished:
        history = record["price_history"]

        for key in (
            "current_price",
            "delta_1w",
            "delta_3m",
            "delta_6m",
            "delta_1y",
            "delta_all",
            "last_updated",
        ):
            assert key in history

    # metadata.json
    metadata_file = output / "metadata.json"

    assert metadata_file.exists()

    metadata = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )

    assert metadata["total_properties"] == len(sample_features)
    assert metadata["properties_with_price"] == 3
    assert metadata["properties_with_area"] == 4

    fin._FINISHED_INDEX_CACHE.clear()


def _latest(root: Path):
    """
    Най-новият timestamped snapshot в дадена папка.
    """

    if not root.exists():
        return None

    candidates = [
        p for p in root.iterdir()
        if p.is_dir()
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda p: p.stat().st_mtime
    )


# ============================================================
# TAG: IDEMPOTENCY
# ============================================================

def test_export_twice_gives_same_property_ids(
    tmp_path,
    monkeypatch,
    sample_features
):
    """
    Два последователни експорта от ЕДНИ И СЪЩИ данни
    трябва да дадат еднакви property_id - иначе price_history
    не може да се изчисли.
    """

    from tools import finished_exporter as fin

    fin._FINISHED_INDEX_CACHE.clear()

    consolidated_dir = (
        tmp_path / "consolidated" / "01-01-2026_10"
    )
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {"id": f"z{i}"},
            "location": {
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        23.3 + i / 1000, 42.7
                    ]
                }
            },
            "price": {
                "value_eur": 100000.0 + i
            }
        }
        for i in range(3)
    ]

    (consolidated_dir / "consolidated.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    monkeypatch.setattr(
        fin, "FINISHED_DIR",
        tmp_path / "finished"
    )
    monkeypatch.setattr(
        fin, "ANALYTICS_DIR",
        tmp_path / "analytics"
    )

    first = json.loads(
        (fin.run_export(
            consolidated_dir=consolidated_dir,
            auto_consolidate=False
        ) / "finished_properties.json").read_text(
            encoding="utf-8"
        )
    )

    fin._FINISHED_INDEX_CACHE.clear()

    second = json.loads(
        (fin.run_export(
            consolidated_dir=consolidated_dir,
            auto_consolidate=False
        ) / "finished_properties.json").read_text(
            encoding="utf-8"
        )
    )

    assert [
        r["property_id"] for r in first
    ] == [
        r["property_id"] for r in second
    ]

    fin._FINISHED_INDEX_CACHE.clear()


# ============================================================
# TAG: DATA QUALITY GATES
# ============================================================

def test_no_record_loses_all_its_data(
    tmp_path,
    monkeypatch,
    sample_features
):
    """
    Нито един запис не бива да изгуби едновременно
    цена, площ и геометрия.
    """

    from tools import finished_exporter as fin

    fin._FINISHED_INDEX_CACHE.clear()

    consolidated_dir = (
        tmp_path / "consolidated" / "01-01-2026_10"
    )
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "source": "sofiaplan",
            "dataset_id": 999,
            "attributes": f["properties"],
            "location": {
                "geometry": f["geometry"]
            }
        }
        for f in sample_features
    ]

    (consolidated_dir / "consolidated.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    monkeypatch.setattr(
        fin, "FINISHED_DIR", tmp_path / "finished"
    )
    monkeypatch.setattr(
        fin, "ANALYTICS_DIR", tmp_path / "analytics"
    )

    output = fin.run_export(
        consolidated_dir=consolidated_dir,
        auto_consolidate=False
    )

    finished = json.loads(
        (output / "finished_properties.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(finished) == len(sample_features)

    for record in finished:
        has_price = record.get("price") is not None
        has_area = record.get("area_m2") is not None
        has_attrs = bool(record.get("attributes"))
        has_geometry = bool(record.get("location"))

        assert has_attrs, "загубени са атрибутите"
        assert has_geometry, "загубена е геометрията"
        assert has_price or has_area or has_attrs

    fin._FINISHED_INDEX_CACHE.clear()
