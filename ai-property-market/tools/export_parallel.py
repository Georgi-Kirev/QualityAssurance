# TAG: EXPORT PARALLEL
# Паралелизира PASS 1 на finished_exporter-а по ядра.
#
# ЗАЩО:
#   finished_exporter беше ЕДНОПРОЦЕСЕН върху 1 781 797 записа.
#   Измерено: ~4 000 записа/сек на едно ядро = ~7.5 мин само
#   за преформатиране, при напълно еднакъв резултат от
#   паралелна обработка.
#
#   Машината има 8 физически ядра. Етапът използваше 1.
#   Това е единствената налична оптимизация - тя използва
#   ЯДРА, не памет.
#
#   КАК РАБОТИ:
#   1. SHARDING (единичен, кратък проход): consolidated.json
#      се разрязва на N JSONL файлове, разпределени
#      round-robin. Това е чист I/O без JSON парсване и е
#      бързо.
#   2. PROCESSING (паралелно): всеки worker обработва СВОЙ
#      shard и пише собствен staging JSONL + собствен index
#      файл с (sort_key, offset). Никой не чака друг.
#   3. MERGE: родителят чете N-те index файла, сортира
#      глобално и записва финалния JSONarray.
#
#   ЗАЩО НЕ Е ПРОСТО "ПУСНИ N ПРОЦЕСА ВЪРХУ ЕДИН ФАЙЛ":
#   записите имат променлива дължина, така че произволно
#   разделяне на файл не е възможно без пълно сканиране.
#   Sharding-ът е правилният подход и е идемпотентен.
#
# РЕЗУЛТАТ: идентичен byte-for-byte с последователния изход.
#   Тест: test_export_parallel_matches_sequential.

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ijson

from tools.finished_exporter import (
    new_area_plausibility,
    observe_area,
)


# TAG: TUNING

# Ако има един-единствен запис, паралелизирането само
# вдига разходите. Под този праг вървим последователно.
MIN_RECORDS_FOR_PARALLEL = 20_000

# Повече workers от това е безсмислено: shard-ът става
# по-малък от I/O буфера и ProcessPool overhead-ът
# изяжда печалбата.
MAX_WORKERS = 8


# TAG: SHARDING

def split_into_shards(
    source_file: Path,
    shard_dir: Path,
    shard_count: int,
) -> List[int]:
    """
    Разрязва JSON масив на shard_count JSONL файла,
    разпределени round-robin.

    Round-robin (не последователни блокове) е умишлено:
    при round-robin всеки shard получава ЕДНАКВО разнообразие
    от записи, а големите полигони (dataset 619 е 255 393
    записа с ~15 000 точки всеки) се разпределят равномерно.
    Последователни блокове би дали на един worker цял
    "тежък" участък и другия - почти празни shard-ове.

    Връща броя записи във всеки shard.
    """
    shard_dir.mkdir(parents=True, exist_ok=True)

    handles = []
    counts = [0] * shard_count

    try:
        for index in range(shard_count):
            handles.append(
                (shard_dir / f"shard_{index}.jsonl").open("wb")
            )

        with source_file.open("rb") as source:
            for position, record in enumerate(
                ijson.items(source, "item", use_float=True)
            ):
                target = position % shard_count
                handles[target].write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        default=str,
                    ).encode("utf-8")
                )
                handles[target].write(b"\n")
                counts[target] += 1
    finally:
        for handle in handles:
            handle.close()

    return counts


# TAG: SHARD WORKER
#
# Функцията е на модулово ниво и приема само прости типове,
# защото Windows използва "spawn": обектите се пикълзват, а
# класове и ламбди не се пренасят.

def process_shard(task: tuple) -> Dict[str, Any]:
    """
    Обработва ЕДИН shard и връща статистика.

    task = (shard_path, staging_path, index_path,
            prior_snapshot_names, timestamp, consolidated_dir,
            work_dir)
    """
    from tools.consolidator import extract_price
    from tools.finished_exporter import (
        generate_property_id,
        load_finished_index,
        parse_snapshot_timestamp,
        select_historical_price,
        compute_price_delta,
        split_area,
    )
    from tools.consolidator import count_non_null
    from datetime import timedelta
    import tools.currency as currency

    (
        shard_path,
        staging_path,
        index_path,
        prior_names,
        timestamp,
        consolidated_dir,
        work_dir,
    ) = task

    # По мрежата пътищата идват като str; локалният извикващ
    # може да е дал Path. Привеждаме ги тук веднъж.
    shard_path = Path(shard_path)
    staging_path = Path(staging_path)
    index_path = Path(index_path)
    work_dir = Path(work_dir)

    started = time.perf_counter()

    # TAG: PRIOR SNAPSHOTS (за price_history)
    #
    # Всеки worker си зарежда индексите сам. Споделянето
    # през pickle би означавало милиони обекти по мрежата.
    # Цената е RAM на worker, но срещу това получаваме
    # N пъти повече CPU.
    from tools.finished_exporter import FINISHED_DIR

    prior_indexes = []
    for name in prior_names:
        snapshot = FINISHED_DIR / name
        if snapshot.exists():
            prior_indexes.append(
                (snapshot, load_finished_index(snapshot))
            )

    from datetime import datetime

    current_ts = datetime.strptime(
        timestamp, "%d-%m-%Y_%H"
    )
    from zoneinfo import ZoneInfo
    current_ts = current_ts.replace(
        tzinfo=ZoneInfo("Europe/Sofia")
    )

    WINDOWS = [
        ("delta_1w", timedelta(days=6), timedelta(days=10)),
        ("delta_3m", timedelta(days=80), timedelta(days=100)),
        ("delta_6m", timedelta(days=165), timedelta(days=195)),
        ("delta_1y", timedelta(days=350), timedelta(days=380)),
    ]

    analytics_path = work_dir / "search_stats.json"
    search_stats: Dict[str, Dict[str, Any]] = {}
    if analytics_path.exists():
        try:
            search_stats = json.loads(
                analytics_path.read_text(encoding="utf-8")
            )
        except Exception:
            search_stats = {}

    assigned_ids: Dict[str, int] = {}
    index_rows: List[tuple] = []
    total = 0
    with_price = 0
    with_total_area = 0
    with_built_area = 0
    price_sum = 0.0
    area_sum = 0.0
    built_sum = 0.0
    area_stats = new_area_plausibility()

    with shard_path.open("rb") as reader, \
            staging_path.open("wb") as sink:

        for line in reader:

            record = json.loads(line)

            prop_id = generate_property_id(record)
            if prop_id in assigned_ids:
                assigned_ids[prop_id] += 1
                prop_id = f"{prop_id}_{assigned_ids[prop_id]}"
            else:
                assigned_ids[prop_id] = 1

            current_price = extract_price(record)
            total_area, built_area = split_area(record)

            price_fields = currency.build_price_fields(
                current_price,
                currency.BASE_CURRENCY,
            )

            price_history: Dict[str, Any] = {
                "current_price": current_price,
                "currency": currency.BASE_CURRENCY,
                "delta_1w": None,
                "delta_3m": None,
                "delta_6m": None,
                "delta_1y": None,
                "delta_all": None,
                "last_updated": timestamp,
            }

            for name, min_age, max_age in WINDOWS:
                hist = select_historical_price(
                    prop_id, current_ts, min_age, max_age,
                    prior_indexes,
                )
                price_history[name] = compute_price_delta(
                    current_price, hist
                )

            if prior_indexes:
                oldest: Optional[float] = None
                for _snap, idx in prior_indexes:
                    if prop_id in idx:
                        p, _meta = idx[prop_id]
                        if p is not None and (
                            oldest is None or p < oldest
                        ):
                            oldest = p
                if oldest is None:
                    for _snap, idx in prior_indexes:
                        if prop_id in idx:
                            p, _meta = idx[prop_id]
                            if p is not None:
                                oldest = p
                                break
                price_history["delta_all"] = compute_price_delta(
                    current_price, oldest
                )

            popularity = search_stats.get(
                prop_id,
                {"view_count": 0, "search_hit_count": 0},
            )

            finished = {
                "property_id": prop_id,
                **record,
                "price": current_price,
                **{
                    f"price_{code.lower()}": value
                    for code, value in price_fields.items()
                },
                "area_m2": total_area,
                "area_total_m2": total_area,
                "area_built_m2": built_area,
                "price_history": price_history,
                "popularity": {
                    "view_count": popularity.get("view_count", 0),
                    "search_hit_count": popularity.get(
                        "search_hit_count", 0
                    ),
                },
                "_snapshot_timestamp": timestamp,
                "_source_consolidated_snapshot": str(
                    consolidated_dir
                ),
            }

            offset = sink.tell()
            sink.write(
                json.dumps(
                    finished,
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
            )
            sink.write(b"\n")

            index_rows.append(
                (
                    (
                        current_price is None,
                        current_price
                        if current_price is not None
                        else float("inf"),
                        total_area is None,
                        -(total_area
                          if total_area is not None else 0.0),
                        -popularity.get("view_count", 0),
                        -count_non_null(finished),
                        total,
                    ),
                    offset,
                )
            )

            total += 1

            if current_price is not None:
                with_price += 1
                price_sum += current_price
            if total_area is not None:
                with_total_area += 1
                area_sum += total_area
            if built_area is not None:
                with_built_area += 1
                built_sum += built_area
            observe_area(area_stats, total_area)

    # TAG: INDEX OUTPUT
    #
    # pickle вместо JSON: sort_key-ът съдържа float и bool,
    # а да го четем обратно като JSON би загубило точността
    # на реда и изискваше пълно разчитане на милиони стойности
    # като текст.
    import pickle

    with index_path.open("wb") as sink:
        pickle.dump(index_rows, sink, protocol=pickle.HIGHEST_PROTOCOL)

    return {
        "shard": shard_path.name,
        "records": total,
        "with_price": with_price,
        "with_total_area": with_total_area,
        "with_built_area": with_built_area,
        "price_sum": price_sum,
        "area_sum": area_sum,
        "built_sum": built_sum,
        "area_stats": area_stats,
        "elapsed": time.perf_counter() - started,
    }


# TAG: RESOURCE PLANNING

def choose_worker_count() -> int:
    """
    Броя worker-и, съобразен с наличните ресурси.

    Използва се същата логика като rest of the pipeline
    (tools.resources), за да е поведението последователно.
    """
    try:
        from tools.resources import plan_workers

        plan = plan_workers()
        if plan.get("allowed") and plan.get("workers"):
            return max(
                1,
                min(MAX_WORKERS, int(plan["workers"])),
            )
    except Exception:
        pass

    return 4


# TAG: CLEANUP

def cleanup_shards(shard_dir: Path) -> None:
    """
    Изтрива shard и staging файловете - те са временни.

    Работи и при непразни вложени папки (shards/ е подпапка
    на work_dir), затова обхождането е дълбоко, а после се
    чисти празните директории отдолу нагоре.
    """
    import shutil

    if not shard_dir.exists():
        return

    # Най-назад изтриваме всичко в дървото.
    for path in sorted(
        shard_dir.rglob("*"),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            # Windows държи файловете отворени за кратко след
            # затваряне. Не е критично - остават temp файлове,
            # които следващият рън презаписва.
            pass

    try:
        shutil.rmtree(shard_dir, ignore_errors=True)
    except Exception:
        pass
