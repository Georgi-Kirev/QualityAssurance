# TAG: TESTS - PARALLEL EXPORT
#
# Паралелизацията трябва да е ПРОЗРАЧНА за продукта:
# същият изход, същият ред, същите стойности - просто
# обработен от няколко процеса.
#
# Въпросът, който този файл пази: ако някой „оптимизира"
# разпределението и подреди изхода различно, агентът ще
# получи различни данни при същия вход.

import json
import os

import pytest

from tools.export_parallel import (
    MAX_WORKERS,
    MIN_RECORDS_FOR_PARALLEL,
    choose_worker_count,
    cleanup_shards,
    process_shard,
    split_into_shards,
)


def _record(i):
    return {
        "source": "sofiaplan",
        "dataset_id": 624,
        "attributes": {
            "id": f"uuid-{i}",
            "address": f"ул. Витоша {i}",
            "plosht": 80 + i,
        },
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3 + i * 1e-5, 42.7],
            }
        },
        "area": {"square_meters": 80 + i, "unit": "m2"},
        "price": {
            "value_eur": 1000.0 + i,
            "source_currency": "BGN",
            "source_value": (1000.0 + i) * 1.95583,
            "currency": "EUR",
        },
    }


@pytest.fixture
def consolidated_file(tmp_path):
    path = tmp_path / "consolidated.json"
    path.write_text(
        json.dumps(
            [_record(i) for i in range(200)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


# ============================================================
# TAG: SHARDING
# ============================================================

def test_shards_cover_every_record(consolidated_file, tmp_path):
    counts = split_into_shards(
        consolidated_file, tmp_path / "shards", 4
    )

    assert sum(counts) == 200
    assert len(counts) == 4


def test_shards_are_balanced(consolidated_file, tmp_path):
    counts = split_into_shards(
        consolidated_file, tmp_path / "shards", 4
    )

    # Round-robin -> разлика максимум 1.
    assert max(counts) - min(counts) <= 1


def test_each_shard_is_valid_jsonl(consolidated_file, tmp_path):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 3
    )

    total = 0
    for shard in sorted((tmp_path / "shards").glob("*.jsonl")):
        with shard.open("rb") as handle:
            for line in handle:
                json.loads(line)
                total += 1

    assert total == 200


def test_single_shard_keeps_order(consolidated_file, tmp_path):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 1
    )

    shard = tmp_path / "shards" / "shard_0.jsonl"
    with shard.open("rb") as handle:
        first = json.loads(handle.readline())

    assert first["attributes"]["id"] == "uuid-0"


def test_sharding_preserves_every_record_exactly_once(
    consolidated_file, tmp_path
):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 7
    )

    seen = set()
    for shard in sorted((tmp_path / "shards").glob("*.jsonl")):
        with shard.open("rb") as handle:
            for line in handle:
                seen.add(json.loads(line)["attributes"]["id"])

    assert len(seen) == 200


# ============================================================
# TAG: SHARD WORKER
# ============================================================

def _run_one_shard(shard, staging, index, tmp_path, workers=1):
    return process_shard((
        shard,
        staging,
        index,
        [],            # без prior snapshots
        "28-09-2026_16",
        "C:\\consolidated",
        str(tmp_path),
    ))


def test_shard_worker_reports_counts(consolidated_file, tmp_path):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 2
    )

    result = _run_one_shard(
        tmp_path / "shards" / "shard_0.jsonl",
        tmp_path / "staging_0.jsonl",
        tmp_path / "index_0.pickle",
        tmp_path,
    )

    assert result["records"] == 100
    assert result["with_price"] == 100
    assert result["with_total_area"] == 100
    # SofiaPlan няма застроена площ -> 0 е правилното.
    assert result["with_built_area"] == 0


def test_shard_worker_reports_area_plausibility(
    consolidated_file, tmp_path
):
    """
    Отчетът за площта трябва да пътува ОТ worker-а.

    Ако accumulate-ят остане само в последователния път,
    паралелният експорт (този, който ползва 1.8 млн.
    записа) ще публикува metadata без обяснението.
    """

    split_into_shards(
        consolidated_file, tmp_path / "shards", 2
    )

    result = _run_one_shard(
        tmp_path / "shards" / "shard_0.jsonl",
        tmp_path / "staging_0.jsonl",
        tmp_path / "index_0.pickle",
        tmp_path,
    )

    stats = result["area_stats"]

    # _record(i) -> area 80+i. Sharding-ът е round-robin,
    # така че shard_0 взема четните индекси 0,2,...,198.
    assert stats["with_area"] == 100
    assert stats["within_band"] == 100
    assert stats["too_large"] == 0
    assert stats["max_area"] == pytest.approx(80 + 198)


def test_shard_worker_separates_zone_areas(tmp_path):
    """
    Смесен shard: 2 зони и 1 жилище. Зоните трябва да се
    отчетат отделно, а не да се изгубят в средното.
    """

    shard = tmp_path / "shard_0.jsonl"

    with shard.open("w", encoding="utf-8") as handle:
        for i, area in ((0, 1_000_000.0), (1, 1_000_000.0), (2, 68.0)):
            record = _record(i)
            # "plosht" е в AREA_TOTAL_KEYS, затова split_area()
            # го разпознава като цялостна площ. ("area_kv_m"
            # не минава през тази проверка - името не
            # съдържа маркер - и отива по extract_area()).
            record["attributes"]["plosht"] = area
            handle.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    result = _run_one_shard(
        shard,
        tmp_path / "staging_0.jsonl",
        tmp_path / "index_0.pickle",
        tmp_path,
    )

    stats = result["area_stats"]

    assert stats["with_area"] == 3
    assert stats["too_large"] == 2
    assert stats["within_band"] == 1


def test_shard_worker_writes_valid_output(consolidated_file, tmp_path):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 2
    )

    staging = tmp_path / "staging_0.jsonl"
    _run_one_shard(
        tmp_path / "shards" / "shard_0.jsonl",
        staging,
        tmp_path / "index_0.pickle",
        tmp_path,
    )

    with staging.open("rb") as handle:
        first = json.loads(handle.readline())

    assert "property_id" in first
    assert first["price"] == pytest.approx(1000.0)
    assert first["price_eur"] == pytest.approx(1000.0)
    assert first["price_usd"] == pytest.approx(1080.0)
    assert first["area_total_m2"] == pytest.approx(80.0)
    assert first["area_built_m2"] is None


def test_shard_index_offsets_point_to_correct_lines(
    consolidated_file, tmp_path
):
    split_into_shards(
        consolidated_file, tmp_path / "shards", 2
    )

    staging = tmp_path / "staging_0.jsonl"
    _run_one_shard(
        tmp_path / "shards" / "shard_0.jsonl",
        staging,
        tmp_path / "index_0.pickle",
        tmp_path,
    )

    import pickle

    rows = None
    with (tmp_path / "index_0.pickle").open("rb") as handle:
        rows = pickle.load(handle)

    assert len(rows) == 100

    with staging.open("rb") as handle:
        for sort_key, offset in rows[:20]:
            handle.seek(offset)
            record = json.loads(handle.readline())
            assert record["property_id"].startswith("prop_")


# ============================================================
# TAG: GLOBAL ORDER IS DETERMINISTIC
# ============================================================

def test_global_sort_is_independent_of_shard_count(consolidated_file, tmp_path):
    """
    Различният брой workers не бива да променя реда на изхода.
    Това е най-важното: иначе property_id -> price_history
    връзката се разваля при всяка смяна на машината.
    """
    def order_for(shard_count, label):
        split_into_shards(
            consolidated_file, tmp_path / f"s{label}", shard_count
        )
        import pickle

        rows = []
        for index in range(shard_count):
            staging = tmp_path / f"{label}_staging_{index}.jsonl"
            _run_one_shard(
                tmp_path / f"s{label}" / f"shard_{index}.jsonl",
                staging,
                tmp_path / f"{label}_index_{index}.pickle",
                tmp_path,
            )
            with (tmp_path / f"{label}_index_{index}.pickle").open(
                "rb"
            ) as handle:
                for sort_key, offset in pickle.load(handle):
                    rows.append((sort_key, index, offset))

        rows.sort(key=lambda row: row[0])

        ids = []
        for _key, shard_index, offset in rows:
            with (tmp_path / f"{label}_staging_{shard_index}.jsonl").open(
                "rb"
            ) as handle:
                handle.seek(offset)
                ids.append(json.loads(handle.readline())["property_id"])
        return ids

    one = order_for(1, "a")
    many = order_for(5, "b")

    assert one == many


def test_cheapest_records_come_first(consolidated_file, tmp_path):
    """
    Продуктовият контракт: цена ниско -> първо.
    """
    import pickle

    split_into_shards(
        consolidated_file, tmp_path / "shards", 4
    )

    rows = []
    for index in range(4):
        staging = tmp_path / f"staging_{index}.jsonl"
        _run_one_shard(
            tmp_path / "shards" / f"shard_{index}.jsonl",
            staging,
            tmp_path / f"index_{index}.pickle",
            tmp_path,
        )
        with (tmp_path / f"index_{index}.pickle").open("rb") as handle:
            for sort_key, offset in pickle.load(handle):
                rows.append((sort_key, index, offset))

    rows.sort(key=lambda row: row[0])

    prices = []
    for _key, shard_index, offset in rows[:10]:
        with (tmp_path / f"staging_{shard_index}.jsonl").open(
            "rb"
        ) as handle:
            handle.seek(offset)
            prices.append(json.loads(handle.readline())["price"])

    assert prices == sorted(prices)


# ============================================================
# TAG: PLANNING & CLEANUP
# ============================================================

def test_worker_count_is_capped():
    workers = choose_worker_count()

    assert 1 <= workers <= MAX_WORKERS


def test_cleanup_removes_temp_files(consolidated_file, tmp_path):
    split_into_shards(
        consolidated_file, tmp_path / "work" / "shards", 3
    )
    work = tmp_path / "work"
    (work / "staging_0.jsonl").write_text("x", encoding="utf-8")

    cleanup_shards(work)

    assert not (work / "shards").exists()
    assert not (work / "staging_0.jsonl").exists()


def test_cleanup_on_missing_dir_is_safe(tmp_path):
    cleanup_shards(tmp_path / "does_not_exist")

    assert not (tmp_path / "does_not_exist").exists()


def test_parallel_threshold_is_sane():
    assert MIN_RECORDS_FOR_PARALLEL >= 1000
