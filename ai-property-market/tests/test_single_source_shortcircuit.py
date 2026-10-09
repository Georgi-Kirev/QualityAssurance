# TAG: TESTS - SINGLE-SOURCE SHORT-CIRCUIT
#
# Бъг/слабост, който този файл държи:
#   match_database() се пускаше дори когато snapshot-ът съдържа
#   само ЕДИН източник. Entity matching има смисъл само при
#   повече от един източник - с един няма втора гледна точка
#   и резултатът е математически 0 съвпадения.
#
#   Наблюдение от реален рън на 28.09.2026: 332 dataset-а,
#   1.78 млн. записа, само sofiaplan -> 55 мин CPU, 10 GB RAM,
#   0 реда в matches_*.jsonl.
#
#   Освен това нямаше прогрес изход, така че процесът изглеждаше
#   окачен и човек нямаше как да прецени дали да чака.

import json

import pytest

from tools import consolidator


def _write_dataset(root, source, dataset_id, records):
    """
    Реалната структура е:
        <dedup_root>/<timestamp>/<source>/dataset_<id>.json

    Това е точно layout-ът, който matcher.find_dataset_files()
    очаква и которыйто identify_dataset() разбира.
    """
    path = (
        root / "28-09-2026_14" / source / f"dataset_{dataset_id}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _record(name, area):
    return {
        "source": "sofiaplan",
        "dataset_id": 1,
        "attributes": {"regname": name, "plosht": area},
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7],
            }
        },
        "area": {"square_meters": area, "unit": "m2"},
    }


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """
    run_consolidation() пише в module-level директории.
    Без изолация тестът би пренаписал реалните storage/ папки.
    """
    dedup_root = tmp_path / "deduplicated"
    cons_root = tmp_path / "consolidated"
    indexes = tmp_path / "indexes"
    matched = tmp_path / "matched"
    for d in (dedup_root, cons_root, indexes, matched):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(consolidator, "CONSOLIDATED_DIR", cons_root)
    monkeypatch.setattr(consolidator, "INDEX_DIR", indexes)
    monkeypatch.setattr(consolidator, "MATCHED_DIR", matched)
    return dedup_root


# ============================================================
# TAG: SOURCE DETECTION
# ============================================================

def test_finds_single_source(isolated_dirs):
    _write_dataset(isolated_dirs, "sofiaplan", 1, [_record("A", 100)])

    snapshot = isolated_dirs / "28-09-2026_14"
    # find_dataset_files очаква <snapshot>/<source>/<ds>/dataset_<id>.json
    sources = consolidator.find_sources_in_snapshot(snapshot)

    assert sources == ["sofiaplan"]


def test_finds_two_sources(isolated_dirs):
    snapshot = isolated_dirs / "28-09-2026_14"
    _write_dataset(isolated_dirs, "sofiaplan", 1, [_record("A", 100)])
    _write_dataset(isolated_dirs, "otherportal", 2, [_record("A", 100)])

    sources = consolidator.find_sources_in_snapshot(snapshot)

    assert sorted(sources) == ["otherportal", "sofiaplan"]


# ============================================================
# TAG: MATCHER IS SKIPPED FOR ONE SOURCE
# ============================================================

def test_matcher_not_called_for_single_source(isolated_dirs, monkeypatch):
    """
    КЛЮЧОВИ ТЕСТ: build_index() и match_database() изобщо не
    бива да се извикват. Те бяха причината за 55 мин и 10 GB.
    """
    _write_dataset(
        isolated_dirs, "sofiaplan", 1,
        [_record("A", 100), _record("B", 200)],
    )

    def explode(*args, **kwargs):
        raise AssertionError(
            "matcher must not run for a single-source snapshot"
        )

    monkeypatch.setattr(consolidator, "build_index", explode)
    monkeypatch.setattr(consolidator, "match_database", explode)

    snapshot = isolated_dirs / "28-09-2026_14"

    result = consolidator.run_consolidation(
        snapshot_dir=snapshot,
        output_dir=isolated_dirs / "out",
        use_existing_matches=False,
    )

    # Пайплайнът трябва да завърши успешно, не да пада.
    assert result is not None
    assert (result / "consolidated.json").exists()


def test_single_source_still_writes_all_records(isolated_dirs, monkeypatch):
    """
    Прескачането на matcher-а НЕ бива да губи записи.
    """
    records = [_record(f"Зона {i}", 100 + i) for i in range(5)]
    _write_dataset(isolated_dirs, "sofiaplan", 1, records)

    monkeypatch.setattr(
        consolidator, "build_index",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )
    monkeypatch.setattr(
        consolidator, "match_database",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )

    snapshot = isolated_dirs / "28-09-2026_14"

    result = consolidator.run_consolidation(
        snapshot_dir=snapshot,
        output_dir=isolated_dirs / "out",
        use_existing_matches=False,
    )

    consolidated = json.loads(
        (result / "consolidated.json").read_text(encoding="utf-8")
    )

    assert len(consolidated) == 5


def test_single_source_creates_empty_matches_file(isolated_dirs, monkeypatch):
    """
    Останалият код чете matches_path. Файлът трябва да съществува,
    за да няма FileNotFoundError по-надолу.
    """
    _write_dataset(isolated_dirs, "sofiaplan", 1, [_record("A", 100)])

    monkeypatch.setattr(
        consolidator, "build_index",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )
    monkeypatch.setattr(
        consolidator, "match_database",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )

    snapshot = isolated_dirs / "28-09-2026_14"

    consolidator.run_consolidation(
        snapshot_dir=snapshot,
        output_dir=isolated_dirs / "out",
        use_existing_matches=False,
    )

    matches = (
        consolidator.MATCHED_DIR / "matches_28-09-2026_14.jsonl"
    )

    assert matches.exists()
    assert matches.read_text(encoding="utf-8") == ""


def test_metadata_records_zero_matches(isolated_dirs, monkeypatch):
    _write_dataset(isolated_dirs, "sofiaplan", 1, [_record("A", 100)])

    monkeypatch.setattr(
        consolidator, "build_index",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )
    monkeypatch.setattr(
        consolidator, "match_database",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")),
    )

    snapshot = isolated_dirs / "28-09-2026_14"

    result = consolidator.run_consolidation(
        snapshot_dir=snapshot,
        output_dir=isolated_dirs / "out",
        use_existing_matches=False,
    )

    metadata = json.loads(
        (result / "clusters_metadata.json").read_text(encoding="utf-8")
    )

    assert metadata["match_pairs"] == 0
    assert metadata["merged_away"] == 0
    assert metadata["total_clusters"] == metadata["total_raw_records"] == 1
