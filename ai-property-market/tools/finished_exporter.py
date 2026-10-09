# TAG: FINISHED EXPORTER
# Взима конзолираните (кластеризирани) записи от
# storage/consolidated/ и ги подготвя за AI агентите.
#
# V1.0:
# - Намира най-новия consolidated snapshot;
# - Генерира стабилен property_id за всеки запис;
# - Сравнява с предишни storage/finished снимки;
# - Изчислява price_history deltas: 1w, 3m, 6m, 1y, all;
# - Сортира: price възходящо -> area низходящо -> popularity;
# - Записва в storage/finished/<timestamp>/finished_properties.json
# - Запазва search_stats.json (с инициализирани нули) и metadata.json

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import ijson


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.consolidator import (
    run_consolidation,
    find_latest_consolidated_snapshot,
    CONSOLIDATED_DIR,
    extract_price,
    count_non_null,
)
from tools.field_intelligence import FIELD_DICTIONARY
from tools import currency


TIMEZONE = ZoneInfo("Europe/Sofia")
FINISHED_DIR = PROJECT_ROOT / "storage" / "finished"
ANALYTICS_DIR = PROJECT_ROOT / "storage" / "analytics"


TIMESTAMP_FORMAT = "%d-%m-%Y_%H"
PRICE_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("price", [])}
AREA_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("area", [])}
ADDRESS_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("address", [])}
NAME_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("name", [])}
ID_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("id", [])}


def create_timestamp() -> str:
    return datetime.now(TIMEZONE).strftime(TIMESTAMP_FORMAT)


def parse_snapshot_timestamp(name: str) -> Optional[datetime]:
    for fmt in (TIMESTAMP_FORMAT, "%d-%m-%Y"):
        try:
            return datetime.strptime(name, fmt).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue
    return None


def find_finished_snapshots() -> List[Path]:
    if not FINISHED_DIR.exists():
        return []
    snaps = [p for p in FINISHED_DIR.iterdir() if p.is_dir()]
    snaps.sort(
        key=lambda p: parse_snapshot_timestamp(p.name) or datetime.min.replace(tzinfo=TIMEZONE)
    )
    return snaps


def find_latest_finished_snapshot() -> Optional[Path]:
    snaps = find_finished_snapshots()
    return snaps[-1] if snaps else None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            cleaned = (
                value.replace(",", ".")
                .replace(" ", "")
                .replace("м2", "")
                .replace("m2", "")
            )
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    return None


def extract_area(record: Dict[str, Any]) -> Optional[float]:
    area_block = record.get("area")
    if isinstance(area_block, dict):
        sm = _coerce_float(area_block.get("square_meters"))
        if sm is not None and sm > 0:
            return sm

    # Fallback само ако нормализаторът не е дал area блок.
    # Ключовете с експлицитна единица (ha/dka/ar) се пропускат -
    # при смесени единици max() даваше напълно грешен резултат
    # (area_dka=500 би станало 500 m2 вместо 50 m2).

    from tools.matcher import (
        detect_area_unit_from_field,
        is_area_field,
    )
    from tools.matcher import area_to_m2

    attrs = record.get("attributes") or {}
    candidates = []
    for k, v in attrs.items():
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if not (kl in AREA_KEYS or "kv_m" in kl or "kvm" in kl or is_area_field(k)):
            continue
        unit = detect_area_unit_from_field(k)
        if unit is None:
            # Без надеждна единица - приемаме само квадратни метри.
            if not (kl in AREA_KEYS or "kv_m" in kl or "kvm" in kl):
                continue
            unit = "m2"
        parsed = area_to_m2(v, unit)
        if parsed is not None and parsed > 0:
            candidates.append(parsed)
    area_candidate = record.get("area_m2")
    parsed_top = _coerce_float(area_candidate)
    if parsed_top is not None and parsed_top > 0:
        candidates.append(parsed_top)
    return max(candidates) if candidates else None


# TAG: AREA SPLIT (цялостна vs застроена)
#
# Имотът обикновено има ДВЕ площи:
#   * цялостна (удебел) - площта на парцела/имота като цяло
#   * застроена         - площта, която реално е построена
#
# Пример: парцел 1 200 m2, къща с застроена площ 180 m2.
# Ако се запише само едно число, агентът не може да прецени
# колко от земята е реално използваемо. Затова изходът носи
# и двете полета.
#
# Правило: търсим изрично наименувано поле за застроена площ.
# Ако няма такова, застроената остава None - по-добре е липсваща
# стойност, отколкото измислена. Никога не се нагадава.

AREA_BUILT_KEYS = (
    "zastroena",
    "zastroen",
    "zas_izg",
    "built",
    "builtup",
    "built_up",
    "footprint",
    "застроена",
    "застроено",
)

AREA_TOTAL_KEYS = (
    "plosht",
    "ploщ",
    "parcel",
    "plot",
    "dvor",
    "dvorasht",
    "uma",
    "land",
    "parcela",
    "имота",
    "участък",
)


def _classify_area_key(key: str) -> str:
    """
    Категоризира име на поле като 'built', 'total' или '' (неясно).
    Проверката за 'built' е ПЪРВА, защото името може да съдържа
    и двата маркера (напр. 'zastroena_uma' -> built печели).
    """
    low = str(key).lower()
    for marker in AREA_BUILT_KEYS:
        if marker in low:
            return "built"
    for marker in AREA_TOTAL_KEYS:
        if marker in low:
            return "total"
    return ""


def _area_unit_from_key(key: str) -> str:
    """
    Единица за площ, изведена от името на полето.

    Първо се пробва matcher-ът (той вече знае кирилица и
    "_kvm" вариантите). Ако не се разпознае, падаме на
    локален разбор.

    ВНИМАНИЕ: "ha" се търси САМО като отделна дума/суфикс.
    Наивно търсене на подниз "ha" хваща "shape", "share",
    "phases" и умножава по 10 000 - точно бъгът, държан от
    test_normalize_area_rejects_non_area_fields.
    """
    from tools.matcher import detect_area_unit_from_field

    try:
        detected = detect_area_unit_from_field(key)
    except Exception:
        detected = None

    if detected:
        return detected

    low = str(key).lower()
    tokens = [
        t for t in re.split(r"[^0-9a-z]+", low) if t
    ]

    # Безопасни поднизи - достатъчно специфични, за да няма
    # ложни съвпадения.
    if "kvm" in low or "m2" in low or "sqm" in low:
        return "m2"
    if "decar" in low:
        return "dka"
    if "hectar" in low:
        return "ha"

    # Отделни токени за dka / ar.
    for token in tokens:
        if token in ("dka", "dekar", "dekari"):
            return "dka"
        if token in ("ar", "ares"):
            return "ar"
        if token in ("ha", "hektar"):
            return "ha"

    return "m2"


def split_area(record: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """
    Връща (area_total_m2, area_built_m2).

    Приоритет:
      1. Поле, изрично наименувано като застроена площ -> built.
      2. Поле, изрично наименувано като цялостна -> total.
      3. Ако изрично цялостна липсва, общият нормализиран area
         блок се приема за цялостна (това е поведението на
         източника по подразбиране).

    Класификацията е по САМОТО ИМЕ на полето. Не разчитаме на
    FIELD_DICTIONARY, защото тя описва SofiaPlan, а имотите от
    бъдещи източници ще имат собствени имена ("plosht_uma",
    "zastroena_plosht"), които речникът още не знае.
    """
    from tools.matcher import area_to_m2

    attrs = record.get("attributes") or {}

    built_candidates: List[float] = []
    total_candidates: List[float] = []

    for key, value in attrs.items():
        if not isinstance(key, str):
            continue

        kind = _classify_area_key(key)

        # Само разпознати като площ имена. Останалите се
        # пропускат - по-добре липсваща стойност, отколкото
        # измислена.
        if kind not in ("built", "total"):
            continue

        parsed = area_to_m2(value, _area_unit_from_key(key))

        if parsed is None or parsed <= 0:
            continue

        if kind == "built":
            built_candidates.append(parsed)
        else:
            total_candidates.append(parsed)

    built = max(built_candidates) if built_candidates else None

    total = (
        max(total_candidates)
        if total_candidates
        else extract_area(record)
    )

    # Застроената никога не надвишава цялостната - иначе данните
    # са противоречиви и агентът ще получи безсмислена стойност.
    if built is not None and total is not None and built > total:
        built = None

    return total, built


# ============================================================
# TAG: ПРАВДОПОДОБИЕ НА ПЛОЩТА
#
# ПРОСЛЕДЕНО, НЕ ГАДАЕНО. Измерено на
# storage\consolidated\28-09-2026_19 (1 849 680 записа,
# 297 170 с площ) на 28.09.2026:
#
#   p1   =          7.8 m2
#   p5   =         34.5 m2
#   p10  =         93.4 m2
#   p25  =        911.1 m2
#   p50  =      9 499.7 m2     <-- МЕДИАНАТА
#   p75  =    264 522.8 m2
#   p90  =    999 912.2 m2     <-- ~1 km2
#   p95  =  1 353 667.2 m2
#   p99  =  6 001 423.0 m2     <-- ~6 km2
#   max  = 13 160 671 157.1 m2
#
# Медиана 9 500 m2 и кръглите стойности около 1 km2 и
# 6 km2 казват точно какво е това: НЕ жилища, а ВЕКТОРНИ
# ЗОНИ - райони и квартали на София. Витоша е ~59 km2,
# Люлин ~3.6 km2, Слатина ~8.5 km2. Числата съвпадат.
#
# СЛЕДСТВИЕ: "average_area_m2 = 522 148" не е бъг в
# превода на единици. Преводат е бил правилен. Числото е
# вярно за това, което е измерено, и безсмислено като
# "средна площ на имот".
#
# ЗАТОВО НЕ ФИЛТРИРАМ ЗАПИСИТЕ. Филтърът би дал по-красиво
# число и би скрил точно факта, че продуктът няма имотни
# обяви. Вместо това тук се БРОЯТ и ПУБЛИКУВАТ двете
# величини - всички площи и само правдоподобните - за да
# може следващият човек да прецени.
#
# Нито един запис не се променя. Това е отчет, не филтър.
# ============================================================

# Долна граница: под 10 m2 не е жилище. Включва WC.
PROPERTY_AREA_MIN_M2 = 10.0

# Горна граница: 2 000 m2 = 0.2 хектара.
#
# ИЗБРАНА ПО ИЗМЕРЕНОТО РАЗПРЕДЕЛЕНИЕ, не на око. При
# този праг остават 32.83% от записите с площ (97 552 от
# 297 170). За сравнение:
#
#     <=   500 m2 -> 19.20%   (едно голово жилище)
#     <= 2 000 m2 -> 32.83%   <- ИЗБРАНОТО
#     <= 5 000 m2 -> 42.77%
#     <= 20 000 m2 -> 57.35%  <- прекалено широко
#
# 20 000 беше първият ми избор и е ГРЕШЕН: при него
# медианата (9 499.7 m2) попада ВЪТРЕ в лентата, т.е.
# типичният запис се брои за правдоподобен, което не е
# вярно. 2 000 държи медианата навън.
#
# ИЗНУЖКАВА СЕ ДА Е ЯСНО: това е ДИАГНОСТИЧНА ЛЕНТА, а не
# филтър. Не променя нито един запис. Ако продуктът
# трябва да приема и големи парцели, константата се
# вдига - но тогава и разпределението трябва да се премери
# отново, вместо да се вярва на число.
PROPERTY_AREA_MAX_M2 = 2000.0


def new_area_plausibility() -> Dict[str, Any]:
    """
    Empty accumulator. Mergable across worker processes.
    """

    return {
        "with_area": 0,
        "area_sum": 0.0,
        "within_band": 0,
        "within_band_sum": 0.0,
        "too_small": 0,
        "too_large": 0,
        "max_area": None,
    }


def observe_area(
    stats: Dict[str, Any],
    area: Optional[float],
) -> None:
    """
    Feed one record's total area into the accumulator.

    Streaming and O(1) in memory: nothing is stored per
    record, so this is safe for 1.8 M records and safe to
    merge by summation from parallel workers.
    """

    if area is None:
        return

    try:
        value = float(area)
    except (TypeError, ValueError):
        return

    if value <= 0:
        return

    stats["with_area"] += 1
    stats["area_sum"] += value

    if stats["max_area"] is None or value > stats["max_area"]:
        stats["max_area"] = value

    if value < PROPERTY_AREA_MIN_M2:
        stats["too_small"] += 1
    elif value > PROPERTY_AREA_MAX_M2:
        stats["too_large"] += 1
    else:
        stats["within_band"] += 1
        stats["within_band_sum"] += value


def merge_area_plausibility(
    parts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Combine accumulators produced by parallel workers.
    """

    total = new_area_plausibility()

    for part in parts:

        total["with_area"] += part.get("with_area", 0)
        total["area_sum"] += part.get("area_sum", 0.0)
        total["within_band"] += part.get("within_band", 0)
        total["within_band_sum"] += part.get("within_band_sum", 0.0)
        total["too_small"] += part.get("too_small", 0)
        total["too_large"] += part.get("too_large", 0)

        part_max = part.get("max_area")

        if part_max is not None and (
            total["max_area"] is None
            or part_max > total["max_area"]
        ):
            total["max_area"] = part_max

    return total


def describe_area_plausibility(
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Turn the accumulator into the published metadata block.

    The note is deliberately blunt. Anyone reading a mean of
    522 148 m2 needs to know that the mean is dominated by
    zone polygons, and that a plausible-property mean exists
    right next to it.
    """

    with_area = stats["with_area"]
    within = stats["within_band"]

    return {
        "plausible_min_m2": PROPERTY_AREA_MIN_M2,
        "plausible_max_m2": PROPERTY_AREA_MAX_M2,
        "records_with_area": with_area,
        "records_within_plausible_band": within,
        "records_below_min": stats["too_small"],
        "records_above_max": stats["too_large"],
        "share_within_band_pct": (
            round(100.0 * within / with_area, 2)
            if with_area
            else None
        ),
        "average_within_band_m2": (
            round(
                stats["within_band_sum"] / within,
                2,
            )
            if within
            else None
        ),
        "largest_area_m2": (
            round(stats["max_area"], 2)
            if stats["max_area"] is not None
            else None
        ),
        "note": (
            "area_kv_m in most SofiaPlan datasets is the area "
            "of an administrative zone / quarter, not of a "
            "dwelling. Measured on snapshot 28-09-2026_19 "
            "(297 170 records with area): p25 911.1 m2, "
            "median 9 499.7 m2, p90 999 912.2 m2, "
            "p99 6 001 423.0 m2. Only 32.83% of records fall "
            "inside the plausible band published here. No "
            "record is filtered or altered by this; the counts "
            "exist so the aggregate is not read as a property "
            "size. See README 'Known limitations'."
        ),
    }


def extract_coordinate_signature(record: Dict[str, Any]) -> str:
    """
    Стaбилен подпис на мястото - O(1), без обхождане на
    геометрията.

    Преди това се обхождаха ВСИЧКИ точки и се взимаше
    най-долният връх. При реални данни (SofiaPlan) един запис
    носи средно 15 332 координати - обхождането струваше
    13.7 ms, т.е. 73 записа/сек и ~7 часа за пълния рън.

    Сега се ползва ПЪРВИЯТ връх. Той е детерминиран и стабилен:
    източникът не пренарежда върховете между два ръна, така че
    property_id остава стабилен - а това е условието за
    price_history да работи (test_export_twice_gives_same_
    property_ids).

    Адресът и площта също влизат в подписа, така че запис без
    геометрия пак се различава.
    """
    loc = record.get("location") or {}
    geom = loc.get("geometry") if isinstance(loc, dict) else None
    if not isinstance(geom, dict):
        return ""
    c = geom.get("coordinates")
    t = geom.get("type")

    point = None

    if t == "Point":
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            point = c
    else:
        # Първият връх на първата двойка вложени координати.
        node = c
        for _ in range(4):
            if (
                isinstance(node, (list, tuple))
                and len(node) >= 2
                and isinstance(node[0], (int, float))
                and isinstance(node[1], (int, float))
            ):
                point = node
                break
            if isinstance(node, (list, tuple)) and node:
                node = node[0]
            else:
                break

    if point is None:
        return ""

    try:
        return f"{float(point[1]):.4f},{float(point[0]):.4f}"
    except (TypeError, ValueError):
        return ""


def extract_address_signature(record: Dict[str, Any]) -> str:
    attrs = record.get("attributes") or {}
    values = []
    for k, v in attrs.items():
        if isinstance(k, str) and k.lower() in ADDRESS_KEYS and isinstance(v, str):
            values.append(v.lower().strip())
    return "|".join(values)


def generate_property_id(record: Dict[str, Any]) -> str:
    parts = []
    attrs = record.get("attributes") or {}
    for k, v in attrs.items():
        if isinstance(k, str) and k.lower() in ID_KEYS:
            parts.append(f"id:{v}")
            break
    coord_sig = extract_coordinate_signature(record)
    if coord_sig:
        parts.append(f"geo:{coord_sig}")
    addr_sig = extract_address_signature(record)
    if addr_sig:
        parts.append(f"addr:{addr_sig[:50]}")
    area = extract_area(record)
    if area is not None:
        parts.append(f"area:{area:.1f}")
    cluster = record.get("_cluster") or {}
    rep = cluster.get("representative")
    if rep:
        parts.append(f"rep:{rep}")
    raw = "||".join(parts) if parts else json.dumps(attrs, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"prop_{h}"


_FINISHED_INDEX_CACHE: Dict[str, Dict[str, Tuple[float, Dict[str, Any]]]] = {}


def load_finished_index(snapshot_path: Path) -> Dict[str, Tuple[float, Dict[str, Any]]]:
    """
    Зарежда index-а на един finished snapshot.

    Резултатът се КЕШИРА по път. Без кеша функцията
    чеше 16 MB finished_properties.json от диск за
    ВСЯКИ запис x всяко time-window (5390 x 5 -> ~27 000
    пълни парсинга), което прави експорта практически
    зависнал.
    """

    cache_key = str(snapshot_path.resolve())

    cached = _FINISHED_INDEX_CACHE.get(cache_key)

    if cached is not None:
        return cached

    index: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    props_file = snapshot_path / "finished_properties.json"
    if not props_file.exists():
        _FINISHED_INDEX_CACHE[cache_key] = index
        return index
    with props_file.open("rb") as fp:
        for record in ijson.items(fp, "item", use_float=True):
            pid = record.get("property_id")
            if not pid:
                continue
            price = extract_price(record)
            snapshot_ts = record.get("_snapshot_timestamp")
            # САМО цената и timestamp-ът. Тук по-рано се пазеше
            # и ЦЕЛИЯТ record за всеки имот от всеки минал
            # snapshot - а после изобщо не се четеше
            # (select_historical_price() хвърля _meta). При
            # 1.78 млн. имота и 2 минали snapshot-а това беше
            # напълно ненужни десетки GB в паметта.
            index[pid] = (
                price,
                {"snapshot_timestamp": snapshot_ts},
            )
    _FINISHED_INDEX_CACHE[cache_key] = index
    return index


def compute_price_delta(
    current_price: Optional[float],
    past_price: Optional[float],
) -> Optional[float]:
    if current_price is None or past_price is None or past_price == 0:
        return None
    return round(((current_price - past_price) / past_price) * 100.0, 2)


def select_historical_price(
    property_id: str,
    current_ts: datetime,
    min_age: timedelta,
    max_age: timedelta,
    prior_indexes: List[Tuple[Path, Dict[str, Tuple[float, Dict[str, Any]]]]],
) -> Optional[float]:
    best_price: Optional[float] = None
    best_diff: Optional[timedelta] = None
    midpoint = (min_age + max_age) / 2
    for snap, idx in prior_indexes:
        snap_ts = parse_snapshot_timestamp(snap.name)
        if snap_ts is None:
            continue
        diff = current_ts - snap_ts
        if not (min_age <= diff <= max_age):
            continue
        if property_id not in idx:
            continue
        price, _meta = idx[property_id]
        if price is None:
            continue
        if best_diff is None or abs(diff - midpoint) < abs(best_diff - midpoint):
            best_price = price
            best_diff = diff
    return best_price


def _should_go_parallel(consolidated_file: Path) -> bool:
    """
    Струва ли си да се паралелизира.

    Под прага (20 000 записа) ProcessPool overhead-ът надвишава
    печалбата. Проверява се и размерът на файла, защото при
    много големи записи sharding-ът сам по себе си е работа.
    """
    from tools.export_parallel import MIN_RECORDS_FOR_PARALLEL

    try:
        size = consolidated_file.stat().st_size
    except OSError:
        return False

    # 50 MB е под консервативна оценка за 20 000 записа
    # (средно 2.5 KB/запис).
    if size < 50 * 1024 * 1024:
        return False

    return size > MIN_RECORDS_FOR_PARALLEL * 100


def _run_parallel_pass(
    consolidated_dir: Optional[Path],
    consolidated_file: Path,
    output_dir: Path,
    timestamp: str,
    prior_finished: List[Path],
    search_stats: Dict[str, Dict[str, Any]],
) -> Optional[Path]:
    """
    Паралелен PASS 1 + общо сортиране.

    Еквивалентен на последователния път - същият код за
    обработка на запис, същият ключ за сортиране, същият изход.
    Разликата е само в това кой процес обработва кое парче.
    """
    from concurrent.futures import ProcessPoolExecutor
    from tools.export_parallel import (
        cleanup_shards,
        choose_worker_count,
        process_shard,
        split_into_shards,
    )

    workers = choose_worker_count()

    work_dir = output_dir / "_parallel_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # search_stats е нужен на всеки worker за popularity.
    stats_file = work_dir / "search_stats.json"
    stats_file.write_text(
        json.dumps(search_stats, ensure_ascii=False),
        encoding="utf-8",
    )

    started_at = time.perf_counter()

    # TAG: STEP 1 — SHARDING
    print(
        f"[FINISHED] Parallel export with {workers} workers. "
        f"Splitting {consolidated_file.name}..."
    )

    counts = split_into_shards(
        consolidated_file,
        work_dir / "shards",
        workers,
    )

    print(
        f"[FINISHED] Sharded in "
        f"{time.perf_counter() - started_at:.0f}s: "
        f"{sum(counts):,} records into {workers} shards "
        f"({min(counts):,}-{max(counts):,} each)."
    )

    # TAG: STEP 2 — PARALLEL PROCESSING
    started_proc = time.perf_counter()

    tasks = []
    for index, shard_count in enumerate(counts):
        if shard_count == 0:
            continue
        tasks.append((
            work_dir / "shards" / f"shard_{index}.jsonl",
            work_dir / f"staging_{index}.jsonl",
            work_dir / f"index_{index}.pickle",
            [snap.name for snap in prior_finished],
            timestamp,
            str(consolidated_dir),
            str(work_dir),
        ))

    results: List[Dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(process_shard, tasks):
            results.append(result)
            print(
                f"[FINISHED]   shard {result['shard']}: "
                f"{result['records']:,} records in "
                f"{result['elapsed']:.0f}s"
            )

    print(
        f"[FINISHED] Processed in "
        f"{time.perf_counter() - started_proc:.0f}s."
    )

    # TAG: STEP 3 — GLOBAL SORT + OUTPUT
    started_write = time.perf_counter()

    import pickle

    all_rows: List[tuple] = []
    for index, shard_count in enumerate(counts):
        if shard_count == 0:
            continue
        index_file = work_dir / f"index_{index}.pickle"
        with index_file.open("rb") as handle:
            for sort_key, offset in pickle.load(handle):
                all_rows.append((sort_key, index, offset))

    all_rows.sort(key=lambda row: row[0])

    out_file = output_dir / "finished_properties.json"
    tmp_file = output_dir / "finished_properties.json.tmp"

    readers = {}
    try:
        for index in range(workers):
            path = work_dir / f"staging_{index}.jsonl"
            if path.exists():
                readers[index] = path.open("rb")

        written = 0
        with tmp_file.open("w", encoding="utf-8") as out:
            out.write("[")
            for position, (_key, shard_index, offset) in enumerate(
                all_rows
            ):
                reader = readers[shard_index]
                reader.seek(offset)
                line = reader.readline().decode("utf-8")
                if written:
                    out.write(",")
                out.write(line)
                written += 1

                if written % 50000 == 0 or written == len(all_rows):
                    print(
                        f"[FINISHED]   wrote {written:,}/"
                        f"{len(all_rows):,} "
                        f"({time.perf_counter() - started_write:.0f}s)"
                    )
            out.write("]")
    finally:
        for reader in readers.values():
            reader.close()

    os.replace(tmp_file, out_file)

    # TAG: AGGREGATE STATISTICS
    total_records = sum(r["records"] for r in results)
    with_price = sum(r["with_price"] for r in results)
    with_total_area = sum(r["with_total_area"] for r in results)
    with_built_area = sum(r["with_built_area"] for r in results)
    price_sum = sum(r["price_sum"] for r in results)
    area_sum = sum(r["area_sum"] for r in results)
    built_sum = sum(r["built_sum"] for r in results)
    area_stats = merge_area_plausibility(
        [r["area_stats"] for r in results]
    )

    cleanup_shards(work_dir)

    _write_metadata_and_link(
        output_dir=output_dir,
        timestamp=timestamp,
        consolidated_dir=consolidated_dir,
        prior_count=len(prior_finished),
        total_records=total_records,
        with_price=with_price,
        with_total_area=with_total_area,
        with_built_area=with_built_area,
        price_sum=price_sum,
        area_sum=area_sum,
        built_sum=built_sum,
        area_stats=area_stats,
    )

    print(
        f"[FINISHED] Exported {total_records:,} properties "
        f"to: {output_dir}"
    )
    print(
        f"[FINISHED] Properties with price: {with_price:,}; "
        f"with total area: {with_total_area:,}; "
        f"with built area: {with_built_area:,}"
    )
    print(
        f"[FINISHED] Total parallel time: "
        f"{time.perf_counter() - started_at:.0f}s "
        f"on {workers} workers."
    )
    return output_dir


def _write_metadata_and_link(
    output_dir: Path,
    timestamp: str,
    consolidated_dir: Optional[Path],
    prior_count: int,
    total_records: int,
    with_price: int,
    with_total_area: int,
    with_built_area: int,
    price_sum: float,
    area_sum: float,
    built_sum: float,
    area_stats: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Записва metadata.json и обновява symlink 'latest'.

    Изнесено, за да няма дублиране между последователния
    и паралелния път.
    """
    metadata = {
        "timestamp": timestamp,
        "source_consolidated_snapshot": str(consolidated_dir),
        "prior_finished_snapshots_used": prior_count,
        "total_properties": total_records,
        "properties_with_price": with_price,
        "properties_with_area": with_total_area,
        "properties_with_total_area": with_total_area,
        "properties_with_built_area": with_built_area,
        "base_currency": currency.BASE_CURRENCY,
        "currencies": currency.describe(),
        "average_price_eur": (
            round(price_sum / with_price, 2)
            if with_price > 0
            else None
        ),
        "average_area_m2": (
            round(area_sum / with_total_area, 2)
            if with_total_area > 0
            else None
        ),
        "average_built_area_m2": (
            round(built_sum / with_built_area, 2)
            if with_built_area > 0
            else None
        ),
    }

    # --------------------------------------------------------
    # TAG: ПРАВДОПОДОБИЕ НА ПЛОЩТА (паралелен път)
    #
    # "average_area_m2" е оставен точно каквото е бил, за да
    # не се счупи никой потребител. Блокът тук казва какво
    # реално стои зад него. Виж измерванията при
    # PROPERTY_AREA_MAX_M2.
    # --------------------------------------------------------

    if area_stats is None:
        area_stats = new_area_plausibility()

    metadata["average_area_m2_is_zone_derived"] = True
    metadata["area_plausibility"] = (
        describe_area_plausibility(area_stats)
    )

    meta_file = output_dir / "metadata.json"
    tmp_meta = output_dir / "metadata.json.tmp"
    with tmp_meta.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta, meta_file)

    latest_link = FINISHED_DIR / "latest"
    if latest_link.exists() or latest_link.is_symlink():
        try:
            latest_link.unlink()
        except Exception:
            pass
    try:
        if hasattr(os, "symlink"):
            os.symlink(
                output_dir, latest_link, target_is_directory=True
            )
    except Exception:
        with (FINISHED_DIR / "latest_pointer.txt").open(
            "w", encoding="utf-8"
        ) as f:
            f.write(str(output_dir))


def run_export(
    consolidated_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    auto_consolidate: bool = True,
) -> Optional[Path]:
    consolidated_dir = consolidated_dir or find_latest_consolidated_snapshot()
    if consolidated_dir is None and auto_consolidate:
        print("[FINISHED] No consolidated snapshot found. Running consolidation...")
        consolidated_dir = run_consolidation()
    if consolidated_dir is None:
        print("[FINISHED] Nothing to export (no consolidated data).")
        return None

    consolidated_file = consolidated_dir / "consolidated.json"
    if not consolidated_file.exists():
        print(f"[FINISHED] Missing consolidated.json in {consolidated_dir}")
        return None

    timestamp = create_timestamp()
    output_dir = output_dir or (FINISHED_DIR / timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

    current_ts = datetime.strptime(timestamp, TIMESTAMP_FORMAT).replace(tzinfo=TIMEZONE)
    all_finished = find_finished_snapshots()
    prior_finished = [p for p in all_finished if parse_snapshot_timestamp(p.name) and parse_snapshot_timestamp(p.name) < current_ts]

    print(f"[FINISHED] Exporting consolidated data from: {consolidated_dir.name}")
    print(f"[FINISHED] Output dir: {output_dir}")
    print(f"[FINISHED] Prior finished snapshots for price history: {len(prior_finished)}")

    analytics_path = ANALYTICS_DIR / "search_stats.json"
    search_stats: Dict[str, Dict[str, Any]] = {}
    if analytics_path.exists():
        try:
            search_stats = json.loads(
                analytics_path.read_text(encoding="utf-8")
            )
        except Exception:
            search_stats = {}

    assigned_ids: Dict[str, int] = {}

    WINDOWS = [
        ("delta_1w", timedelta(days=6), timedelta(days=10)),
        ("delta_3m", timedelta(days=80), timedelta(days=100)),
        ("delta_6m", timedelta(days=165), timedelta(days=195)),
        ("delta_1y", timedelta(days=350), timedelta(days=380)),
    ]

    # --------------------------------------------------------
    # Всички prior индекси се зареждат ОДИН път, преди
    # цикъла по записи. load_finished_index() е кеширан,
    # но и така би имало N x S кеш lookup-а.
    # --------------------------------------------------------

    prior_indexes: List[
        Tuple[Path, Dict[str, Tuple[float, Dict[str, Any]]]]
    ] = [
        (snap, load_finished_index(snap))
        for snap in prior_finished
    ]

    # --------------------------------------------------------
    # TAG: PASS 1 — STREAM TO STAGING
    #
    # Преди това: list(ijson.items(...)) + finished_records.append
    # -> ДВА пълни списъка от 1 781 797 записа едновременно.
    # Наблюдение 28.09.2026: 13.65 GB RAM и без изход.
    #
    # Сега: записът се изсипва ред по ред във временен JSONL и в
    # паметта остава само (sort_key, byte_offset).
    # --------------------------------------------------------

    staging_path = output_dir / "_staging.jsonl"

    parallel = _should_go_parallel(consolidated_file)

    if parallel:
        return _run_parallel_pass(
            consolidated_dir=consolidated_dir,
            consolidated_file=consolidated_file,
            output_dir=output_dir,
            timestamp=timestamp,
            prior_finished=prior_finished,
            search_stats=search_stats,
        )

    # (sort_key, offset)
    write_index: List[tuple] = []

    total_records = 0
    with_price = 0
    with_total_area = 0
    with_built_area = 0
    price_sum = 0.0
    area_sum = 0.0
    built_sum = 0.0
    area_stats = new_area_plausibility()

    started_at = time.perf_counter()

    print(f"[FINISHED] Pass 1/2: streaming {consolidated_file.name}...")

    with consolidated_file.open("rb") as src, \
            staging_path.open("wb") as sink:

        for record in ijson.items(
            src, "item", use_float=True
        ):

            prop_id = generate_property_id(record)
            if prop_id in assigned_ids:
                assigned_ids[prop_id] += 1
                prop_id = f"{prop_id}_{assigned_ids[prop_id]}"
            else:
                assigned_ids[prop_id] = 1

            current_price = extract_price(record)
            total_area, built_area = split_area(record)

            # Цената се държи в EUR като канонична стойност.
            # Останалите валути се добавят автоматично от
            # tools.currency - добавяне на нова валута става с
            # една промяна там, не тук.
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
                hist_price = select_historical_price(
                    prop_id, current_ts, min_age, max_age,
                    prior_indexes,
                )
                price_history[name] = compute_price_delta(
                    current_price, hist_price
                )

            if prior_indexes:
                oldest_price: Optional[float] = None
                for _snap, idx in prior_indexes:
                    if prop_id in idx:
                        p, _ = idx[prop_id]
                        if p is not None and (
                            oldest_price is None or p < oldest_price
                        ):
                            oldest_price = p
                if oldest_price is None:
                    for _snap, idx in prior_indexes:
                        if prop_id in idx:
                            p, _ = idx[prop_id]
                            if p is not None:
                                oldest_price = p
                                break
                price_history["delta_all"] = compute_price_delta(
                    current_price, oldest_price
                )

            popularity = search_stats.get(
                prop_id,
                {"view_count": 0, "search_hit_count": 0},
            )

            finished = {
                "property_id": prop_id,
                **record,
                # Канонична цена (EUR) + автоматичните преводи.
                # "price" остава за съвместимост с вече издадени
                # snapshot-и и със search_api.
                "price": current_price,
                **{
                    f"price_{code.lower()}": value
                    for code, value in price_fields.items()
                },
                # Площ: area_m2 остава alias на цялостната площ.
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
                "_source_consolidated_snapshot": str(consolidated_dir),
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

            write_index.append(
                (
                    (
                        current_price is None,
                        current_price
                        if current_price is not None
                        else float("inf"),
                        total_area is None,
                        -(total_area if total_area is not None else 0.0),
                        -popularity.get("view_count", 0),
                        -count_non_null(finished),
                        total_records,
                    ),
                    offset,
                )
            )

            total_records += 1

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

            if total_records % 20000 == 0:
                print(
                    f"[FINISHED]   staged {total_records:,} "
                    f"({time.perf_counter() - started_at:.0f}s)"
                )

    print(
        f"[FINISHED] Pass 1 done: {total_records:,} records "
        f"in {time.perf_counter() - started_at:.0f}s."
    )

    # --------------------------------------------------------
    # TAG: PASS 2 — SORTED OUTPUT
    # --------------------------------------------------------

    print("[FINISHED] Pass 2/2: writing sorted output...")

    write_index.sort(key=lambda entry: entry[0])

    out_file = output_dir / "finished_properties.json"
    tmp_file = output_dir / "finished_properties.json.tmp"

    started_write = time.perf_counter()
    written = 0

    with staging_path.open("rb") as reader, \
            tmp_file.open("w", encoding="utf-8") as f:

        f.write("[")

        for _sort_key, offset in write_index:

            reader.seek(offset)
            line = reader.readline().decode("utf-8")

            if written:
                f.write(",")

            f.write(line)
            written += 1

            if written % 20000 == 0 or written == len(
                write_index
            ):
                print(
                    f"[FINISHED]   wrote {written:,}/"
                    f"{len(write_index):,} "
                    f"({time.perf_counter() - started_write:.0f}s)"
                )

        f.write("]")

    os.replace(tmp_file, out_file)
    staging_path.unlink(missing_ok=True)
    write_index.clear()
    assigned_ids.clear()

    metadata = {
        "timestamp": timestamp,
        "source_consolidated_snapshot": str(consolidated_dir),
        "prior_finished_snapshots_used": len(prior_finished),
        "total_properties": total_records,
        "properties_with_price": with_price,
        "properties_with_area": with_total_area,
        "properties_with_total_area": with_total_area,
        "properties_with_built_area": with_built_area,
        "base_currency": currency.BASE_CURRENCY,
        "currencies": currency.describe(),
        "average_price_eur": (
            round(price_sum / with_price, 2)
            if with_price > 0
            else None
        ),
        "average_area_m2": (
            round(area_sum / with_total_area, 2)
            if with_total_area > 0
            else None
        ),
        "average_built_area_m2": (
            round(built_sum / with_built_area, 2)
            if with_built_area > 0
            else None
        ),
    }

    # --------------------------------------------------------
    # TAG: ПРАВДОПОДОБИЕ НА ПЛОЩТА (последователен път)
    #
    # "average_area_m2" е оставен точно каквото е бил, за да
    # не се счупи никой потребител. Блокът тук казва какво
    # реално стои зад него. Виж измерванията при
    # PROPERTY_AREA_MAX_M2.
    # --------------------------------------------------------

    metadata["average_area_m2_is_zone_derived"] = True
    metadata["area_plausibility"] = (
        describe_area_plausibility(area_stats)
    )

    meta_file = output_dir / "metadata.json"
    tmp_meta = output_dir / "metadata.json.tmp"
    with tmp_meta.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta, meta_file)

    latest_link = FINISHED_DIR / "latest"
    if latest_link.exists() or latest_link.is_symlink():
        try:
            latest_link.unlink()
        except Exception:
            pass
    try:
        if hasattr(os, "symlink"):
            os.symlink(output_dir, latest_link, target_is_directory=True)
    except Exception:
        with (FINISHED_DIR / "latest_pointer.txt").open("w", encoding="utf-8") as f:
            f.write(str(output_dir))

    print(f"[FINISHED] Exported {total_records:,} properties to: {output_dir}")
    print(f"[FINISHED] Properties with price: {with_price:,}; with total area: {with_total_area:,}; with built area: {with_built_area:,}")
    return output_dir


def get_latest_finished_file() -> Optional[Path]:
    snap = find_latest_finished_snapshot()
    if not snap:
        return None
    f = snap / "finished_properties.json"
    return f if f.exists() else None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export consolidated data to storage/finished.")
    parser.add_argument("--consolidated", type=str, default=None, help="Path/name of consolidated snapshot")
    parser.add_argument("--no-auto-consolidate", action="store_true", help="Don't auto-run consolidation")
    args = parser.parse_args()

    consol = None
    if args.consolidated:
        p = Path(args.consolidated)
        consol = p if p.is_absolute() else (CONSOLIDATED_DIR / args.consolidated)

    out = run_export(
        consolidated_dir=consol,
        auto_consolidate=not args.no_auto_consolidate,
    )
    if out:
        print(f"SUCCESS: {out}")
    else:
        print("FAILED")
        sys.exit(1)
