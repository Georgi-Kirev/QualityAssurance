# TAG: CONSOLIDATOR
# Използва резултата от Matcher за да групира
# записите, които описват един и същ реален обект,
# и избира най-добрия (най-изгодният) представител
# за всеки кластер.
#
# V1.0:
# - Намира най-новия DEDUPLICATED snapshot;
# - Използва matcher.build_index + match_database;
# - Union-Find кластеризация за MATCH двойки;
# - POSSIBLE/RELATED остават като "unresolved" (за ръчна проверка);
# - Избор на representative по:
#     1. най-ниска price (ако има price поле)
#     2. най-богати данни (най-много не-null атрибути)
# - Записва consolidation.json (канонични записи) + clusters_metadata.json

import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import ijson


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.matcher import (
    build_index,
    match_database,
    find_dataset_files,
    identify_dataset,
    DEDUPLICATED_DIR,
    INDEX_DIR,
    MATCHED_DIR,
)
from tools.field_intelligence import FIELD_DICTIONARY
from tools import currency


TIMEZONE = ZoneInfo("Europe/Sofia")
CONSOLIDATED_DIR = PROJECT_ROOT / "storage" / "consolidated"


def create_timestamp() -> str:
    return datetime.now(TIMEZONE).strftime("%d-%m-%Y_%H")


PRICE_KEYS = {key.lower() for key in FIELD_DICTIONARY.get("price", [])}

# Фиксиран курс БНБ. Пазен за съвместимост с код, който все още
# го ползва директно. Новата конверсия минава през tools.currency,
# за да може да се добавяват валути на едно място.
BGN_TO_EUR = 1.95583


def find_deduplicated_snapshots() -> List[Path]:
    if not DEDUPLICATED_DIR.exists():
        return []
    snapshots = [p for p in DEDUPLICATED_DIR.iterdir() if p.is_dir()]
    snapshots.sort(key=lambda p: p.stat().st_mtime)
    return snapshots


def find_latest_deduplicated_snapshot() -> Optional[Path]:
    snapshots = find_deduplicated_snapshots()
    return snapshots[-1] if snapshots else None


def _to_positive_float(value: Any) -> Optional[float]:
    """
    Строго превръща стойност в положително float.

    Търпи Decimal и numeric string, защото точно тях
    емайкират от ijson + json.dumps(default=str) и
    преди това водеха до нула цени без следа.
    """

    if value is None or isinstance(
        value,
        bool
    ):
        return None

    if isinstance(
        value,
        (int, float, Decimal)
    ):
        parsed = float(value)
    elif isinstance(
        value,
        str
    ):
        try:
            parsed = float(
                value.strip()
                .replace(",", ".")
                .replace(" ", "")
                .replace("\u20ac", "")
                .replace("EUR", "")
                .replace("BGN", "")
                .replace("лв.", "")
            )
        except (TypeError, ValueError):
            return None
    else:
        return None

    if not math.isfinite(parsed) or parsed <= 0:
        return None

    return parsed


def extract_price(record: Dict[str, Any]) -> Optional[float]:
    """
    Цена на записа, ВИНАГИ в EUR.

    Ред на доверие:
      1. price.source_value + price.source_currency - най-явното
         (нормализаторът вече пише това, когато полето не е
         изрично в EUR).
      2. price.value_eur - вече конвертирано.
      3. price.raw_value - с конверсия по валутата на източника.
      4. Атрибутите като последен вариант.

    Всички тези стойности се връщат в EUR, защото
    finished_exporter и price_history работят само с EUR.
    """
    price_block = record.get("price")

    # finished_properties.json записва "price" като СКАЛАР
    # (float/None), не като dict блок. Без този клон
    # price_history винаги е празна - функцията не можеше
    # да прочете собствения си изход.
    if price_block is not None and not isinstance(
        price_block,
        dict
    ):

        scalar = _to_positive_float(
            price_block
        )

        if scalar is not None:
            return scalar

    if isinstance(price_block, dict):

        # 1) Изрична двойка стойност + валута (най-надеждна).
        source_value = price_block.get("source_value")
        source_currency = price_block.get("source_currency")
        if source_value is not None and source_currency:
            converted = currency.to_eur(
                _to_positive_float(source_value),
                source_currency,
            )
            if converted is not None:
                return round(converted, 2)

        # 2) Вече конвертирано в EUR.
        for key in ("value_eur", "price_eur", "eur"):
            parsed = _to_positive_float(
                price_block.get(key)
            )
            if parsed is not None:
                return parsed

        # 3) Сурова стойност -> конверсия по валутата на източника.
        raw = _to_positive_float(price_block.get("raw_value"))
        if raw is not None:
            declared = price_block.get("currency")
            origin = (
                str(declared).strip().upper()
                if declared
                else currency.source_currency(record.get("source"))
            )
            converted = currency.to_eur(raw, origin)
            if converted is not None:
                return round(converted, 2)

    attrs = record.get("attributes") or {}
    native = currency.source_currency(record.get("source"))
    candidates = {}
    for k, v in attrs.items():
        if not (isinstance(k, str) and k.lower() in PRICE_KEYS):
            continue
        parsed = _to_positive_float(v)
        if parsed is None:
            continue
        # Полета с изричен валутен суфикс са най-надеждни.
        # Останалите се четат в естествената валута на източника -
        # за SofiaPlan това е BGN. Без това цена в лева
        # се маркираше като EUR и беше ~2x твърде голяма.
        kl = k.lower()
        if "bgn" in kl or "лв" in kl:
            field_currency = "BGN"
        elif "eur" in kl or "€" in kl:
            field_currency = "EUR"
        else:
            field_currency = native
        converted = currency.to_eur(parsed, field_currency)
        if converted is not None:
            candidates[k] = converted
    if not candidates:
        return None
    return min(candidates.values())


# TAG: RICHNESS KEYS THAT DO NOT COUNT
#
# "Богатство" на записа = колко ИНФОРМАЦИЯ носи, не колко
# байта. Геометрията е ЕДИН факт ("има граница"), а не 15 000
# отделни стойности.
#
# Наблюдение от реален рън (28.09.2026): реалните записи от
# SofiaPlan носят средно 15 332 координати (~457 KB) всеки.
# Рекурсивното обхождане на тях даваше 9 ms само за
# count_non_null -> ~110 записа/сек -> часове за пълния рън.
#
# ВНИМАНИЕ: interceptва се САМО "geometry", не "location".
# "location" е обикновен контейнер и трябва да се обходи, за
# да стигнем до вложената геометрия.
GEOMETRY_KEYS = {"geometry"}


def count_non_null(record: Dict[str, Any]) -> int:
    def walk(value, key: str = "") -> int:
        if key in GEOMETRY_KEYS:
            # Един факт, че има геометрия - не 15 000 точки.
            return 1 if value else 0
        if value is None:
            return 0
        if isinstance(value, dict):
            return sum(walk(v, k) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return sum(walk(v, key) for v in value)
        if isinstance(value, str):
            return 1 if value.strip() else 0
        return 1
    return walk(record)


def make_record_uid(source: str, dataset_id: Any, record_index: int) -> str:
    return f"{source}:{dataset_id}:{record_index}"


def parse_record_uid(uid: str) -> Tuple[str, str, int]:
    parts = uid.split(":", 2)
    source, dataset_id, ridx = parts[0], parts[1], int(parts[2])
    return source, dataset_id, ridx


class DSU:
    def __init__(self):
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def iter_records_with_index(dataset_path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    with dataset_path.open("rb") as fp:
        for idx, record in enumerate(ijson.items(fp, "item", use_float=True)):
            yield idx, record


def find_sources_in_snapshot(snapshot_dir: Path) -> List[str]:
    """Всички различни източници, присъстващи в snapshot-а."""
    sources: Set[str] = set()
    for file_path in find_dataset_files(snapshot_dir):
        source, _dataset_id = identify_dataset(
            snapshot_dir,
            file_path,
        )
        sources.add(source)
    return sorted(sources)


def run_consolidation(
    snapshot_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    use_existing_matches: bool = True,
) -> Optional[Path]:
    snapshot_dir = snapshot_dir or find_latest_deduplicated_snapshot()
    if snapshot_dir is None or not snapshot_dir.exists():
        print("[CONSOLIDATOR] No deduplicated snapshot found.")
        return None

    timestamp = create_timestamp()
    output_dir = output_dir or (CONSOLIDATED_DIR / timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    MATCHED_DIR.mkdir(parents=True, exist_ok=True)
    db_path = INDEX_DIR / f"consolidator_{snapshot_dir.name}.db"
    matches_path = MATCHED_DIR / f"matches_{snapshot_dir.name}.jsonl"

    # --------------------------------------------------------
    # TAG: SINGLE-SOURCE SHORT-CIRCUIT
    #
    # Entity matching има смисъл САМО когато има повече от
    # един източник. С един източник няма втора гледна точка
    # за съпоставка и резултатът е математически 0 съвпадения -
    # а цената е реален ръб, време и десетки GB RAM.
    #
    # Наблюдение от реален рън на 28.09.2026: 332 dataset-а,
    # 1.78 млн. записа, само sofiaplan. match_database() изразходва
    # 55 мин CPU и 10 GB RAM, за да произведе 0 реда.
    #
    # Дубликатите ВЪТРЕ един източник вече се ловят по-добре от
    # deduplicator.content_fingerprint() - по семантични
    # признаци, не по 3 514 реда блокираща логика.
    # --------------------------------------------------------

    sources = find_sources_in_snapshot(snapshot_dir)

    skip_matcher = len(sources) <= 1

    if skip_matcher:
        print(
            f"[CONSOLIDATOR] Snapshot has a single source "
            f"({sources[0] if sources else 'unknown'}). "
            f"Skipping matcher: cross-source entity matching is "
            f"meaningless with one source and cannot produce "
            f"matches. Intra-source duplicates are already "
            f"removed by the deduplicator."
        )
        # Празен файл, за да не счупи останалия код, който
        # чете matches_path по-надолу.
        if not matches_path.exists():
            matches_path.write_text("", encoding="utf-8")

    elif use_existing_matches and matches_path.exists() and db_path.exists():
        print(f"[CONSOLIDATOR] Reusing existing matches: {matches_path.name}")
    else:
        print(f"[CONSOLIDATOR] Building matcher index for: {snapshot_dir.name}")
        indexed = build_index(snapshot_dir, db_path)
        if indexed == 0:
            print("[CONSOLIDATOR] No records indexed. Aborting.")
            return None
        print(f"[CONSOLIDATOR] Running match_database...")
        counts = match_database(db_path, matches_path)
        print(f"[CONSOLIDATOR] Match counts: {counts}")

    # --------------------------------------------------------
    # TAG: PASS 1 — STREAM, DON'T MATERIALIZE
    #
    # Преди това всеки запис се слагаше в Python dict
    # (all_records[uid] = record). При 1 781 788 записа от
    # реален рън (28.09.2026) това е ~10 GB RAM и 20+ минути
    # без изход - наблюдавано два пъти.
    #
    # Сега: записът се изсипва ред по ред във временен JSONL и
    # в паметта остава САМО лек индекс
    #     (uid, price, richness, sort_key, byte_offset)
    # После редът се чете обратно чрез seek, когато се пише
    # изходният файл. Паметта пада от десетки GB на стотици MB
    # независимо от броя записи.
    # --------------------------------------------------------

    print("[CONSOLIDATOR] Pass 1/2: streaming records to staging file...")

    dataset_files = find_dataset_files(snapshot_dir)

    staging_path = output_dir / "_staging.jsonl"

    # (uid, price, richness, sort_key, offset)
    index: List[tuple] = []

    total_raw_records = 0

    started_at = time.perf_counter()

    with staging_path.open("wb") as sink:

        for file_number, file_path in enumerate(
            dataset_files,
            start=1,
        ):

            source, dataset_id = identify_dataset(
                snapshot_dir,
                file_path,
            )

            for record_index, record in iter_records_with_index(
                file_path
            ):

                uid = make_record_uid(
                    source,
                    dataset_id,
                    record_index,
                )

                price = extract_price(record)
                richness = count_non_null(record)

                offset = sink.tell()

                sink.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        default=str,
                    ).encode("utf-8")
                )
                sink.write(b"\n")

                sort_key = (
                    (
                        price is not None,
                        price if price is not None
                        else float("inf"),
                    ),
                    -richness,
                    total_raw_records,
                )

                index.append(
                    (uid, price, richness, sort_key, offset)
                )

                total_raw_records += 1

            if file_number % 25 == 0 or file_number == len(
                dataset_files
            ):
                print(
                    f"[CONSOLIDATOR]   staged "
                    f"{file_number}/{len(dataset_files)} datasets, "
                    f"{total_raw_records:,} records, "
                    f"{time.perf_counter() - started_at:.0f}s"
                )

    print(
        f"[CONSOLIDATOR] Staged {total_raw_records:,} records "
        f"in {time.perf_counter() - started_at:.0f}s."
    )

    # Позиция на всеки uid в index - за бърз достъп при
    # избор на представител и при prices_seen.
    uid_index: Dict[str, int] = {
        entry[0]: position
        for position, entry in enumerate(index)
    }

    # --------------------------------------------------------
    # TAG: MATCH PAIRS -> DSU
    # --------------------------------------------------------

    print("[CONSOLIDATOR] Reading match pairs and building DSU clusters...")

    dsu = DSU()
    match_pairs = 0
    possible_pairs = 0
    related_pairs = 0
    unresolved_pairs: List[Dict[str, Any]] = []

    known_uids = {entry[0] for entry in index}

    if matches_path.exists():
        with matches_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pair = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = pair.get("status")
                uid_a = make_record_uid(
                    pair.get("source_a"),
                    pair.get("dataset_a"),
                    pair.get("record_index_a"),
                )
                uid_b = make_record_uid(
                    pair.get("source_b"),
                    pair.get("dataset_b"),
                    pair.get("record_index_b"),
                )
                if uid_a not in known_uids or uid_b not in known_uids:
                    continue
                if status == "MATCH":
                    dsu.union(uid_a, uid_b)
                    match_pairs += 1
                elif status == "POSSIBLE":
                    possible_pairs += 1
                    unresolved_pairs.append(pair)
                elif status == "RELATED":
                    related_pairs += 1
                    unresolved_pairs.append(pair)

    # --------------------------------------------------------
    # TAG: CLUSTER MEMBERSHIP
    # --------------------------------------------------------

    # При един източник няма сливания - всеки запис е свой
    # собствен клъстер. Няма смисъл да се държи речник с
    # 1.78 млн. ключа.
    cluster_members: Dict[str, List[str]] = {}
    representative_uid: Dict[str, str] = {}

    if skip_matcher:

        for uid, price, richness, sort_key, offset in index:
            representative_uid[uid] = uid
            cluster_members[uid] = [uid]

    else:

        for entry in index:
            uid = entry[0]
            root = dsu.find(uid)
            cluster_members.setdefault(root, []).append(uid)

        # Представител: най-ниска цена, после най-много полета.
        for root, members in cluster_members.items():
            best_uid: Optional[str] = None
            best_price: Optional[float] = None
            best_richness: Optional[int] = None
            for uid in members:
                _uid, price, richness, _sk, _off = index[uid_index[uid]]
                if best_uid is None:
                    best_uid, best_price, best_richness = (
                        uid,
                        price,
                        richness,
                    )
                    continue
                better = False
                if price is not None and best_price is None:
                    better = True
                elif (
                    price is not None
                    and best_price is not None
                    and price < best_price
                ):
                    better = True
                elif (
                    price is None
                    and best_price is None
                    and richness > (best_richness or 0)
                ):
                    better = True
                elif (
                    price is not None
                    and best_price is not None
                    and price == best_price
                    and richness > (best_richness or 0)
                ):
                    better = True
                if better:
                    best_uid, best_price, best_richness = (
                        uid,
                        price,
                        richness,
                    )
            representative_uid[root] = best_uid

    total_clusters = len(cluster_members)
    merged_away = total_raw_records - total_clusters

    print(f"[CONSOLIDATOR] Clusters: {total_clusters:,}; Merged records: {merged_away:,};")
    print(f"[CONSOLIDATOR] MATCH pairs: {match_pairs:,}; POSSIBLE: {possible_pairs:,}; RELATED: {related_pairs:,};")

    # --------------------------------------------------------
    # TAG: PASS 2 — WRITE OUTPUT IN SORTED ORDER
    # --------------------------------------------------------

    print("[CONSOLIDATOR] Pass 2/2: writing sorted output...")

    consolidated_path = output_dir / "consolidated.json"
    tmp_path = output_dir / "consolidated.json.tmp"
    clusters_path = output_dir / "clusters_summary.json"
    tmp_cl = output_dir / "clusters_summary.json.tmp"

    # Само представителите отиват в изхода.
    selected = [
        entry
        for entry in index
        if representative_uid.get(
            dsu.find(entry[0]) if not skip_matcher else entry[0]
        ) == entry[0]
    ]

    selected.sort(key=lambda entry: entry[3])

    written = 0
    started_write = time.perf_counter()

    with staging_path.open("rb") as reader, \
            tmp_path.open("w", encoding="utf-8") as out_f, \
            tmp_cl.open("w", encoding="utf-8") as cl_f:

        out_f.write("[")
        cl_f.write("[")

        for position, (uid, price, richness, sort_key, offset) in enumerate(
            selected
        ):

            reader.seek(offset)
            raw = reader.readline()
            record = json.loads(raw)

            root = uid if skip_matcher else dsu.find(uid)
            members = cluster_members[root]

            record["_cluster"] = {
                "size": len(members),
                "members": members,
                "representative": representative_uid[root],
            }

            if len(members) > 1:
                prices_seen: Dict[str, Any] = {}
                for member_uid in members:
                    if uid_index.get(member_uid) is not None:
                        _u, member_price, _r, _s, _o = index[
                            uid_index[member_uid]
                        ]
                        if member_price is not None:
                            prices_seen[member_uid] = member_price
                record["_cluster"]["prices_seen"] = prices_seen

            if written:
                out_f.write(",")
                cl_f.write(",")

            out_f.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
            )
            cl_f.write(
                json.dumps(
                    {
                        "representative": representative_uid[root],
                        "size": len(members),
                        "members": members,
                    },
                    ensure_ascii=False,
                )
            )

            written += 1

            if written % 10000 == 0 or written == len(selected):
                print(
                    f"[CONSOLIDATOR]   wrote {written:,}/"
                    f"{len(selected):,} "
                    f"({time.perf_counter() - started_write:.0f}s)"
                )

        out_f.write("]")
        cl_f.write("]")

    os.replace(tmp_path, consolidated_path)
    os.replace(tmp_cl, clusters_path)

    # --------------------------------------------------------
    # TAG: CLEANUP
    # --------------------------------------------------------

    staging_path.unlink(missing_ok=True)
    index.clear()
    known_uids.clear()
    cluster_members.clear()
    representative_uid.clear()
    uid_index.clear()

    metadata = {
        "timestamp": timestamp,
        "source_snapshot": str(snapshot_dir),
        "sources": sources,
        "total_raw_records": total_raw_records,
        "total_clusters": total_clusters,
        "merged_away": merged_away,
        "match_pairs": match_pairs,
        "possible_pairs": possible_pairs,
        "related_pairs": related_pairs,
        "unresolved_count": len(unresolved_pairs),
        "matcher_skipped_single_source": skip_matcher,
    }
    metadata_path = output_dir / "clusters_metadata.json"
    tmp_meta = output_dir / "clusters_metadata.json.tmp"
    with tmp_meta.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta, metadata_path)

    unresolved_path = output_dir / "unresolved_pairs.json"
    tmp_unres = output_dir / "unresolved_pairs.json.tmp"
    with tmp_unres.open("w", encoding="utf-8") as f:
        json.dump(unresolved_pairs, f, ensure_ascii=False, indent=2)
    os.replace(tmp_unres, unresolved_path)

    print(f"[CONSOLIDATOR] Done. Output: {output_dir}")
    return output_dir


def find_consolidated_snapshots() -> List[Path]:
    if not CONSOLIDATED_DIR.exists():
        return []
    snapshots = [p for p in CONSOLIDATED_DIR.iterdir() if p.is_dir()]
    snapshots.sort(key=lambda p: p.stat().st_mtime)
    return snapshots


def find_latest_consolidated_snapshot() -> Optional[Path]:
    snapshots = find_consolidated_snapshots()
    return snapshots[-1] if snapshots else None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Consolidate matcher matches into unique property clusters.")
    parser.add_argument("--snapshot", type=str, default=None, help="Deduplicated snapshot dir name or path")
    parser.add_argument("--fresh", action="store_true", help="Rebuild matcher index and matches")
    args = parser.parse_args()

    snap = None
    if args.snapshot:
        p = Path(args.snapshot)
        snap = p if p.is_absolute() else (DEDUPLICATED_DIR / args.snapshot)

    result = run_consolidation(
        snapshot_dir=snap,
        use_existing_matches=not args.fresh,
    )
    if result:
        print(f"SUCCESS: {result}")
    else:
        print("FAILED")
        sys.exit(1)
