
# TAG: DEDUPLICATOR
# РЈРЅРёРІРµСЂСЃР°Р»РµРЅ РёРЅСЃС‚СЂСѓРјРµРЅС‚ Р·Р° РїСЂРµРјР°С…РІР°РЅРµ РЅР° С‚РѕС‡РЅРё РґСѓР±Р»РёРєР°С‚Рё
# РѕС‚ NORMALIZED РґР°РЅРЅРё.
#
# V1.2:
# - РЅР°РјРёСЂР° РЅР°Р№-РЅРѕРІРёСЏ NORMALIZED snapshot;
# - РѕР±СЂР°Р±РѕС‚РІР° РІСЃРёС‡РєРё dataset С„Р°Р№Р»РѕРІРµ;
# - СЂР°Р±РѕС‚Рё streaming;
# - РЅРµ Р·Р°СЂРµР¶РґР° С†РµР»РёСЏ dataset РІ RAM;
# - СЃСЉР·РґР°РІР° SHA-256 fingerprint РЅР° РІСЃРµРєРё record;
# - РїСЂРµРјР°С…РІР° С‚РѕС‡РЅРё РґСѓР±Р»РёРєР°С‚Рё;
# - Р·Р°РїР°Р·РІР° РїСЉСЂРІРёСЏ СЃСЂРµС‰РЅР°С‚ record;
# - Р·Р°РїРёСЃРІР° timestamped DEDUPLICATED snapshot;
# - СЃСЉР·РґР°РІР° metadata/report;
# - РёРјР° Р°РІС‚РѕРјР°С‚РёС‡РµРЅ --test СЂРµР¶РёРј;
# - РЅРµ РїСЂРѕРјРµРЅСЏ NORMALIZED РґР°РЅРЅРёС‚Рµ.
#
# Р’РђР–РќРћ:
# Deduplication != Entity Matching
#
# Deduplication:
#   "РўРµР·Рё РґРІР° Р·Р°РїРёСЃР° СЃР° Р°Р±СЃРѕР»СЋС‚РЅРѕ РµРґРЅР°РєРІРё."
#
# Matching:
#   "РўРµР·Рё РґРІР° СЂР°Р·Р»РёС‡РЅРё Р·Р°РїРёСЃР° РІРµСЂРѕСЏС‚РЅРѕ РѕРїРёСЃРІР°С‚ РµРґРёРЅ Рё СЃСЉС‰ РёРјРѕС‚."
#
# Matching С‰Рµ Р±СЉРґРµ РѕС‚РґРµР»РµРЅ РµС‚Р°Рї.


# TAG: IMPORTS

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import ijson


# TAG: PROJECT ROOT

BASE_DIR = Path(__file__).resolve().parent.parent

# content_fingerprint() ползва нормализацията от
# tools.matcher, за да дедупликацията и entity matching-ът да
# разпознават "един и същ адрес" по един и същ начин. Затова
# коренът на проекта трябва да е в sys.path - иначе модулът
# зависи от това как е стартиран.
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# TAG: STORAGE PATHS

NORMALIZED_DIR = BASE_DIR / "storage" / "normalized"
DEDUPLICATED_DIR = BASE_DIR / "storage" / "deduplicated"


# TAG: CONFIGURATION

TIMEZONE = ZoneInfo("Europe/Sofia")

EXCLUDED_FINGERPRINT_FIELDS = {
    # Р‘СЉРґРµС‰Рё volatile fields РјРѕРіР°С‚ РґР° Р±СЉРґР°С‚ РґРѕР±Р°РІРµРЅРё С‚СѓРє.
    # "processed_at",
    # "run_id",
}


# TAG: FORMATTING

def format_number(value: int) -> str:
    return f"{value:,}"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes = seconds / 60

    if minutes < 60:
        return f"{minutes:.1f}m"

    hours = minutes / 60

    return f"{hours:.2f}h"


# TAG: TIMESTAMP

def create_timestamp() -> str:
    now = datetime.now(TIMEZONE)

    return now.strftime("%d-%m-%Y_%H")


# TAG: SNAPSHOT DISCOVERY

def find_normalized_snapshots() -> list[Path]:
    """
    Р’СЉСЂРЅР° СЃР°РјРѕ Р—РђР’РЄР РЁР•РќР normalizer run-РѕРІРµ.

    Normalizer-СЉС‚ РїРёС€Рµ metadata.json СЃР°РјРѕ РїСЂРё СѓСЃРїРµС€РµРЅ
    Р·Р°РІСЉСЂС€РµРЅ run. РџСЂРµРєСЉСЃРЅР°С‚ РёР»Рё СЃС‡СѓРїРµРЅ run РѕСЃС‚Р°РІР° Р±РµР·
    metadata.json Рё С‚СЂСЏР±РІР° РґР° Р±СЉРґРµ РР“РќРћР РР РђРќ, РёРЅР°С‡Рµ
    dedup С‰Рµ СЂР°Р±РѕС‚Рё СЃ РЅРµРїСЉР»РЅРё РґР°РЅРЅРё.
    """

    if not NORMALIZED_DIR.exists():
        return []

    snapshots = [
        path
        for path in NORMALIZED_DIR.iterdir()
        if path.is_dir()
        and (path / "metadata.json").exists()
    ]

    snapshots.sort(
        key=lambda path: path.stat().st_mtime
    )

    return snapshots


def find_latest_normalized_snapshot() -> Path | None:

    snapshots = find_normalized_snapshots()

    if not snapshots:
        return None

    return snapshots[-1]


# TAG: DATASET DISCOVERY

def find_dataset_files(snapshot_dir: Path) -> list[Path]:

    dataset_files = []

    for path in snapshot_dir.rglob("dataset_*.json"):

        if path.is_file():
            dataset_files.append(path)

    dataset_files.sort()

    return dataset_files


# TAG: CANONICALIZATION

def remove_excluded_fields(value):

    if isinstance(value, dict):

        result = {}

        for key in sorted(value.keys()):

            if key in EXCLUDED_FINGERPRINT_FIELDS:
                continue

            result[key] = remove_excluded_fields(
                value[key]
            )

        return result

    if isinstance(value, list):

        return [
            remove_excluded_fields(item)
            for item in value
        ]

    return value


def canonical_json(record: dict) -> bytes:

    cleaned_record = remove_excluded_fields(
        record
    )

    serialized = json.dumps(
        cleaned_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str
    )

    return serialized.encode("utf-8")


# TAG: FINGERPRINT

def calculate_fingerprint(record: dict) -> str:

    canonical_data = canonical_json(record)

    return hashlib.sha256(
        canonical_data
    ).hexdigest()


# TAG: CONTENT FINGERPRINT (СЃРµРјР°РЅС‚РёС‡РµРЅ, РЅРµ РїРѕР±Р°Р№С‚РѕРІ)
#
# Р—РђР©Рћ Р• РќРЈР–РќРћ:
#   calculate_fingerprint() С…РµС€РёСЂР° Р¦Р•Р›РРЇ Р·Р°РїРёСЃ. Р’СЃРµРєРё РёР·С‚РѕС‡РЅРёРє
#   СЃР»Р°РіР° СЃРѕР±СЃС‚РІРµРЅРѕ `id`, СЃРѕР±СЃС‚РІРµРЅРѕ `dataset_id` Рё СЃРѕР±СЃС‚РІРµРЅРѕ
#   `source`, Р·Р°С‚РѕРІР° РµРґРёРЅ Рё СЃСЉС‰ СЂРµР°Р»РµРЅ РѕР±РµРєС‚ РѕС‚ РґРІР° РёР·С‚РѕС‡РЅРёРєР°
#   Р”РђР’Рђ Р РђР—Р›РР§Р•Рќ SHA-256. РЎР»РµРґРѕРІР°С‚РµР»РЅРѕ РїРѕР±Р°Р№С‚РѕРІРёСЏС‚ fingerprint
#   РЎРўР РЈРљРўРЈР РќРћ РќР• РњРћР–Р• РґР° РїСЂРµРјР°С…РЅРµ РєСЂРѕСЃСЃ-РёР·С‚РѕС‡РЅРёРєРѕРІРё РґСѓР±Р»РёРєР°С‚Рё -
#   С‚РѕРІР° РЅРµ Рµ Р±СЉРі, Р° РіСЂРµС€РµРЅ РёР·Р±РѕСЂ РЅР° Р°Р»РіРѕСЂРёС‚СЉРј.
#
#   РћСЃРІРµРЅ С‚РѕРІР° process_dataset() СЃСЉР·РґР°РІР°С€Рµ РќРћР’ seen_set Р·Р° РІСЃРµРєРё
#   dataset С„Р°Р№Р», С‚Р°РєР° С‡Рµ Рё РІ СЂР°РјРєРёС‚Рµ РЅР° РµРґРёРЅ СЂСЉРЅ РЅСЏРјР°С€Рµ РєР°Рє РґР°
#   СЃРµ СЃСЂР°РІРЅСЏС‚ РґРІР° СЂР°Р·Р»РёС‡РЅРё С„Р°Р№Р»Р°.
#
# Р Р•РЁР•РќРР•:
#   content_fingerprint() СЃС‚СЂРѕРё РєР»СЋС‡ СЃР°РјРѕ РѕС‚ РЎР•РњРђРќРўРР§РќР РїСЂРёР·РЅР°С†Рё
#   (Р°РґСЂРµСЃ, РєРѕРѕСЂРґРёРЅР°С‚Рё, РїР»РѕС‰, С‚РёРї). РџР»СЋСЃ fingerprint-СЉС‚ СЃРµ РїР°Р·Рё
#   РІ Р•Р”РРќ РѕР±С‰ set Р·Р° С†РµР»РёСЏ СЂСЉРЅ.
#
# Р‘Р•Р—РћРџРђРЎРќРћРЎРў:
#   РќРµ РІСЃРµРєРё Р·Р°РїРёСЃ РјРѕР¶Рµ РґР° СЃРµ РґРµРґСѓРїР»РёС†РёСЂР°. РР·РёСЃРєРІР°РјРµ РїРѕРЅРµ Р”Р’Рђ
#   РЅРµР·Р°РІРёСЃРёРјРё РїСЂРёР·РЅР°РєР°. РЎР°РјРѕ РєРѕРѕСЂРґРёРЅР°С‚Рё РёР»Рё СЃР°РјРѕ РїР»РѕС‰ РќР• СЃР°
#   РґРѕСЃС‚Р°С‚СЉС‡РЅРё - С‚РѕРІР° Рµ СЃСЉС‰РёСЏС‚ РїСЂРёРЅС†РёРї, РєРѕР№С‚Рѕ РґСЉСЂР¶Рё matcher-Р°
#   (MIN_WEIGHT_FOR_MATCH = 30) Рё СЃСЉС‰РёС‚Рµ СЂРµРіСЂРµСЃСЃРёРѕРЅРЅРё С‚РµСЃС‚РѕРІРµ
#   ("test_coordinates_only_is_never_match").
#
#   Р—Р°РїРёСЃ Р±РµР· РїРѕРЅРµ РµРґРёРЅ РЅР°РґРµР¶РґРµРЅ РїСЂРёР·РЅР°Рє РїРѕР»СѓС‡Р°РІР° fingerprint
#   None Рё РќРРљРћР“Рђ РЅРµ СЃРµ СЃР»РёРІР°. РџСЂРµРґРїРѕС‡РёС‚Р°РјРµ РїСЂРѕРїСѓСЃРЅР°С‚Р°
#   РґРµРґСѓРїР»РёРєР°С†РёСЏ РїСЂРµРґ С„Р°Р»С€РёРІРѕ СЃР»РёРІР°РЅРµ.

# TAG: CONTENT DEDUP SCOPE (read this before changing the rule)
#
# Content fingerprint dedup is applied ONLY BETWEEN DIFFERENT
# SOURCES. Inside one source, two records sharing address,
# geometry and area are almost always DIFFERENT FACTS.
#
# Real case (run of 28.09.2026): dataset 624
# (ikonomika.imoti_ceni_ge) holds price PER YEAR for each zone.
# 2,830 records, only 148 distinct zones -> ~19 rows per zone
# (2002, 2016, 2019, 2020...), all with the SAME geometry,
# SAME address and SAME area.
#
# The first version merged them all and dataset 624 came out
# with 0 records - the entire price layer vanished from the
# product, with no error and no warning.
#
# Rule of thumb: content dedup exists to merge what DIFFERENT
# agencies publish about the same object. It is NOT a
# time-series compactor and must never drop records coming
# from the same source.

COORD_PRECISION = 4          # ~11 m РІ РЎРѕС„РёСЏ
AREA_RELATIVE_BUCKETS = 100  # СЃС‚СЉРїРєР° РѕС‚ 1% Р·Р° РїР»РѕС‰С‚Р°


def _round_area_bucket(area_m2: float) -> int:
    if area_m2 <= 0:
        return 0
    return int(round(area_m2 / AREA_RELATIVE_BUCKETS))


def _record_area_m2(record: dict) -> float | None:
    """
    РџР»РѕС‰ РІ mВІ РѕС‚ РЅРѕСЂРјР°Р»РёР·РёСЂР°РЅРёСЏ Р·Р°РїРёСЃ.

    РџСЂРµРґРїРѕС‡РёС‚Р° РіРѕС‚РѕРІРёСЏ `area` Р±Р»РѕРє, Р·Р°С‰РѕС‚Рѕ matcher-СЉС‚ СЂР°Р±РѕС‚Рё СЃ
    СЃСѓСЂ Attributes Рё РјРѕР¶Рµ РґР° РІСЉСЂРЅРµ РґСЂСѓРіРѕ С‡РёСЃР»Рѕ.
    """

    area_block = record.get("area")

    if isinstance(area_block, dict):

        for key in ("square_meters", "value"):

            raw = area_block.get(key)

            if isinstance(
                raw,
                (int, float)
            ) and not isinstance(raw, bool):

                if raw > 0:
                    return float(raw)

    return None


def _record_centroid(record: dict) -> tuple | None:
    """
    Р¦РµРЅС‚СЂРѕРёРґ РЅР° РіРµРѕРјРµС‚СЂРёСЏС‚Р°, Р·Р°РєСЂСЉРіР»РµРЅ РґРѕ COORD_PRECISION.

    Р’Р·РµРјР° РќРђР™-Р›Р•Р’РРЇРў РґРѕР»РµРЅ СЉРіСЉР», Р° РЅРµ СЃСЂРµРґРЅР°С‚Р° С‚РѕС‡РєР°, Р·Р°С‰РѕС‚Рѕ
    Р·Р°РїРёСЃСЉС‚ РјРѕР¶Рµ РґР° Рµ Polygon РёР»Рё MultiPolygon, Р° РїСЉСЂРІРёСЏС‚
    РІСЂСЉС… Рµ РґРµС‚РµСЂРјРёРЅРёСЂР°РЅ Рё РµРґРЅР°РєСЉРІ Р·Р° РµРґРЅР° Рё СЃСЉС‰Р° РіРµРѕРјРµС‚СЂРёСЏ
    РѕС‚ СЂР°Р·Р»РёС‡РЅРё РёР·С‚РѕС‡РЅРёС†Рё.
    """

    location = record.get("location")

    if not isinstance(location, dict):
        return None

    geometry = location.get("geometry")

    if not isinstance(geometry, dict):
        return None

    coordinates = geometry.get("coordinates")

    if not isinstance(coordinates, (list, tuple)):
        return None

    points = []

    def walk(node) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            points.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    try:
        walk(coordinates)
    except (TypeError, ValueError):
        return None

    if not points:
        return None

    lon = min(p[0] for p in points)
    lat = min(p[1] for p in points)

    return (
        round(lat, COORD_PRECISION),
        round(lon, COORD_PRECISION),
    )


def _record_address(record: dict) -> str | None:
    """
    РќРѕСЂРјР°Р»РёР·РёСЂР°РЅ Р°РґСЂРµСЃ.

    РР·РїРѕР»Р·РІР° СЃСЉС‰Р°С‚Р° РЅРѕСЂРјР°Р»РёР·Р°С†РёСЏ РєР°С‚Рѕ matcher-Р°, Р·Р° РґР° РґРµРґСѓРїР»РёРєР°С†РёСЏС‚Р°
    Рё entity matching-СЉС‚ РґР° РіРѕРІРѕСЂРµС‚ РЅР° РµРґРёРЅ РµР·РёРє Р·Р° "РµРґРёРЅ Рё СЃСЉС‰ Р°РґСЂРµСЃ".
    """

    try:
        from tools.matcher import extract_address
    except Exception:
        return None

    try:
        address = extract_address(record)
    except Exception:
        return None

    if not address:
        return None

    normalized = str(address).strip().lower()

    if len(normalized) < 4:
        return None

    return normalized


def _record_property_type(record: dict) -> str:
    try:
        from tools.matcher import extract_property_type
    except Exception:
        return ""

    try:
        value = extract_property_type(record)
    except Exception:
        return ""

    if not value:
        return ""

    return str(value).strip().lower()[:40]


def content_fingerprint(record: dict) -> str | None:
    """
    РЎРµРјР°РЅС‚РёС‡РµРЅ fingerprint.

    Р’СЂСЉС‰Р° SHA-256 СЃР°РјРѕ Р°РєРѕ Р·Р°РїРёСЃСЉС‚ РёРјР° РґРѕСЃС‚Р°С‚СЉС‡РЅРѕ РїСЂРёР·РЅР°С†Рё.
    Р’СЂСЉС‰Р° None Р°РєРѕ РЅСЏРјР° РєР°РєРІРѕ РЅР°РґРµР¶РґРЅРѕ РґР° СЃСЂР°РІРЅСЏРІР°РјРµ - С‚РѕРіР°РІР°
    Р·Р°РїРёСЃСЉС‚ СЃРµ Р·Р°РїР°Р·РІР° Р±РµР· РґР° СѓС‡Р°СЃС‚РІР° РІ РґРµРґСѓРїР»РёРєР°С†РёСЏС‚Р°.
    """

    address = _record_address(record)
    centroid = _record_centroid(record)
    area = _record_area_m2(record)
    ptype = _record_property_type(record)

    signals = sum(
        1
        for value in (address, centroid, area)
        if value
    )

    # РњРёРЅРёРјСѓРј РґРІР° РЅРµР·Р°РІРёСЃРёРјРё РїСЂРёР·РЅР°РєР°. Р•РґРёРЅ-РµРґРёРЅСЃС‚РІРµРЅ РїСЂРёР·РЅР°Рє
    # (РЅР°РїСЂ. СЃР°РјРѕ РєРѕРѕСЂРґРёРЅР°С‚Рё) Рµ С‚РІСЉСЂРґРµ СЃР»Р°Р± - РІСЃРёС‡РєРё Р·Р°РїРёСЃРё РѕС‚
    # РµРґРёРЅ Рё СЃСЉС‰ СЃР»РѕР№ СЃРїРѕРґРµР»СЏС‚ РіРµРѕРјРµС‚СЂРёСЏ Рё Р±РёС…Р° СЃРµ СЃР»СЏР»Рё РјР°СЃРѕРІРѕ.
    if signals < 2:
        return None

    parts = [
        f"addr:{address or ''}",
        f"geo:{centroid[0]:.4f},{centroid[1]:.4f}"
        if centroid
        else "geo:",
        f"area:{_round_area_bucket(area)}"
        if area
        else "area:",
        f"type:{ptype}",
    ]

    return hashlib.sha256(
        "|".join(parts).encode("utf-8")
    ).hexdigest()


# TAG: STREAMING READER

def read_normalized_records(file_path: Path):

    with file_path.open("rb") as file:

        records = ijson.items(
            file,
            "item",
            use_float=True
        )

        for record in records:
            yield record


# TAG: STREAMING WRITER

def write_json_record(
    file,
    record: dict,
    first_record: bool
):

    if not first_record:
        file.write(b",")

    data = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str
    ).encode("utf-8")

    file.write(data)


# TAG: DATASET PROCESSOR

def process_dataset(
    input_file: Path,
    output_file: Path,
    seen_exact: set | None = None,
    content_sources: dict | None = None,
    source: str = "unknown",
) -> dict:

    start_time = time.perf_counter()

    input_records = 0
    output_records = 0
    duplicate_records = 0
    exact_duplicates = 0
    content_duplicates = 0
    unidentifiable_records = 0

    # TAG: FINGERPRINT INDEX
    #
    # seen_exact  - глобален за целия рън. Побайтови дубликати
    #               се махат навсякъде, вкл. в един източник:
    #               иначе няма защо да ги има в списъка.
    #
    # content_sources - fingerprint -> множество от източници,
    #               които вече са го произвели. Записът е дубликат
    #               САМО ако fingerprint-ът се среща при ДРУГ
    #               източник. Виж TAG: CONTENT FINGERPRINT -
    #               така не се унищожават времевите серии.
    if seen_exact is None:
        seen_exact = set()
    if content_sources is None:
        content_sources = {}

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_file = output_file.with_suffix(
        output_file.suffix + ".tmp"
    )

    try:

        with temporary_file.open("wb") as output:

            output.write(b"[")

            first_output_record = True

            for record in read_normalized_records(
                input_file
            ):

                input_records += 1

                if not isinstance(record, dict):

                    raise ValueError(
                        f"Invalid record #{input_records}: "
                        f"expected object, "
                        f"got {type(record).__name__}"
                    )

                exact = calculate_fingerprint(
                    record
                )

                if exact in seen_exact:

                    duplicate_records += 1
                    exact_duplicates += 1

                    continue

                content = content_fingerprint(
                    record
                )

                if content is None:

                    unidentifiable_records += 1

                else:

                    origins = content_sources.get(
                        content
                    )

                    if (
                        origins
                        and any(
                            s != source
                            for s in origins
                        )
                    ):

                        duplicate_records += 1
                        content_duplicates += 1

                        continue

                seen_exact.add(exact)

                if content is not None:

                    content_sources.setdefault(
                        content,
                        set(),
                    ).add(source)

                write_json_record(
                    output,
                    record,
                    first_output_record
                )

                first_output_record = False

                output_records += 1

            output.write(b"]")

        # TAG: ATOMIC OUTPUT

        os.replace(
            temporary_file,
            output_file
        )

    except Exception:

        if temporary_file.exists():
            temporary_file.unlink()

        raise

    elapsed = (
        time.perf_counter() - start_time
    )

    return {
        "input_records": input_records,
        "output_records": output_records,
        "duplicate_records": duplicate_records,
        "exact_duplicates": exact_duplicates,
        "content_duplicates": content_duplicates,
        "unidentifiable_records": unidentifiable_records,
        "elapsed_seconds": elapsed,
        "input_size_bytes": input_file.stat().st_size,
        "output_size_bytes": output_file.stat().st_size
    }


# TAG: METADATA

def create_run_metadata(
    input_snapshot: Path,
    output_snapshot: Path,
    dataset_results: list[dict],
    started_at: str,
    finished_at: str
) -> dict:

    total_input = sum(
        item["input_records"]
        for item in dataset_results
    )

    total_output = sum(
        item["output_records"]
        for item in dataset_results
    )

    total_duplicates = sum(
        item["duplicate_records"]
        for item in dataset_results
    )

    total_exact_duplicates = sum(
        item.get("exact_duplicates", 0)
        for item in dataset_results
    )

    total_content_duplicates = sum(
        item.get("content_duplicates", 0)
        for item in dataset_results
    )

    total_unidentifiable = sum(
        item.get("unidentifiable_records", 0)
        for item in dataset_results
    )

    total_input_bytes = sum(
        item["input_size_bytes"]
        for item in dataset_results
    )

    total_output_bytes = sum(
        item["output_size_bytes"]
        for item in dataset_results
    )

    successful = sum(
        1
        for item in dataset_results
        if item["status"] == "SUCCESS"
    )

    failed = sum(
        1
        for item in dataset_results
        if item["status"] == "FAILED"
    )

    return {
        "tool": "deduplicator",
        "version": "1.2",

        "started_at": started_at,
        "finished_at": finished_at,

        "input": {
            "type": "normalized_snapshot",
            "snapshot": str(input_snapshot)
        },

        "output": {
            "type": "deduplicated_snapshot",
            "snapshot": str(output_snapshot)
        },

        "datasets": {
            "total": len(dataset_results),
            "successful": successful,
            "failed": failed
        },

        "records": {
            "input": total_input,
            "output": total_output,
            "duplicates_removed": total_duplicates,
            "exact_duplicates_removed": total_exact_duplicates,
            "content_duplicates_removed": total_content_duplicates,
            "skipped_no_identity_signals": total_unidentifiable
        },

        "files": {
            "input_bytes": total_input_bytes,
            "output_bytes": total_output_bytes
        },

        "dataset_results": dataset_results
    }


# TAG: REAL DATA PROCESS

def run_deduplication() -> int:

    started_datetime = datetime.now(
        TIMEZONE
    )

    started_at = started_datetime.isoformat()

    print()
    print("================================")
    print(" DEDUPLICATOR V1.2")
    print("================================")
    print()

    # TAG: FIND INPUT

    latest_snapshot = (
        find_latest_normalized_snapshot()
    )

    if latest_snapshot is None:

        print(
            "[ERROR] No NORMALIZED snapshots found."
        )

        print()
        print("Expected:")
        print(NORMALIZED_DIR)

        return 1

    print(
        "[INFO] Latest NORMALIZED snapshot:"
    )

    print(latest_snapshot)

    print()

    # TAG: FIND DATASETS

    dataset_files = find_dataset_files(
        latest_snapshot
    )

    if not dataset_files:

        print(
            "[ERROR] No normalized dataset files found."
        )

        return 1

    print(
        f"[INFO] Normalized datasets found: "
        f"{len(dataset_files)}"
    )

    print()

    # TAG: CREATE OUTPUT

    timestamp = create_timestamp()

    output_snapshot = (
        DEDUPLICATED_DIR /
        timestamp
    )

    output_snapshot.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        "[INFO] Output directory:"
    )

    print(output_snapshot)

    print()

    # TAG: PROCESS DATASETS
    #
    # РћР±С‰РёС‚Рµ РјРЅРѕР¶РµСЃС‚РІР° СЃРµ СЃСЉР·РґР°РІР°С‚ РћР”РРќ РџРЄРў Р·Р° С†РµР»РёСЏ СЂСЉРЅ.
    # РўРµ СЃР° РїСЂРёС‡РёРЅР°С‚Р° РґРµРґСѓРїР»РёРєР°С†РёСЏС‚Р° РґР° РІРёР¶РґР° Р·Р°РїРёСЃРёС‚Рµ РѕС‚ РІСЃРёС‡РєРё
    # dataset С„Р°Р№Р»РѕРІРµ РєР°С‚Рѕ РµРґРЅР° РѕР±С‰РЅРѕСЃС‚, Р° РЅРµ РєР°С‚Рѕ РёР·РѕР»РёСЂР°РЅРё
    # С„Р°Р№Р»РѕРІРµ. Р’РёР¶ TAG: CONTENT FINGERPRINT РіРѕСЂРµ.
    seen_exact: set = set()
    content_sources: dict = {}

    dataset_results = []

    for index, input_file in enumerate(
        dataset_files,
        start=1
    ):

        relative_path = input_file.relative_to(
            latest_snapshot
        )

        output_file = (
            output_snapshot /
            relative_path
        )

        source = (
            relative_path.parts[0]
            if len(relative_path.parts) > 1
            else "unknown"
        )

        dataset_id = input_file.stem.replace(
            "dataset_",
            "",
            1
        )

        print(
            f"[{index}/{len(dataset_files)}] "
            f"{source}/{dataset_id}"
        )

        print(
            f"      Input: {input_file}"
        )

        try:

            statistics = process_dataset(
                input_file,
                output_file,
                seen_exact=seen_exact,
                content_sources=content_sources,
                source=source,
            )

            result = {
                "source": source,
                "dataset_id": dataset_id,
                "status": "SUCCESS",

                "input_file": str(input_file),
                "output_file": str(output_file),

                **statistics
            }

            dataset_results.append(
                result
            )

            print(
                f"      [OK] "
                f"{format_number(statistics['input_records'])} "
                f"input"
            )

            print(
                f"      [OK] "
                f"{format_number(statistics['output_records'])} "
                f"output"
            )

            print(
                f"      [DUPLICATES] "
                f"{format_number(statistics['duplicate_records'])} "
                f"total "
                f"({format_number(statistics['exact_duplicates'])} exact + "
                f"{format_number(statistics['content_duplicates'])} content)"
            )

            if statistics["unidentifiable_records"] > 0:
                print(
                    f"      [SKIPPED-DEDUP] "
                    f"{format_number(statistics['unidentifiable_records'])} "
                    f"without identity signals"
                )

            print(
                f"      [TIME] "
                f"{format_duration(statistics['elapsed_seconds'])}"
            )

        except Exception as error:

            result = {
                "source": source,
                "dataset_id": dataset_id,
                "status": "FAILED",

                "input_file": str(input_file),
                "output_file": str(output_file),

                "input_records": 0,
                "output_records": 0,
                "duplicate_records": 0,
                "exact_duplicates": 0,
                "content_duplicates": 0,
                "unidentifiable_records": 0,

                "input_size_bytes": 0,
                "output_size_bytes": 0,

                "elapsed_seconds": 0,

                "error": (
                    f"{type(error).__name__}: "
                    f"{error}"
                )
            }

            dataset_results.append(
                result
            )

            print(
                f"      [FAILED] "
                f"{type(error).__name__}: {error}"
            )

        print()

    # TAG: FINAL METADATA

    finished_datetime = datetime.now(
        TIMEZONE
    )

    finished_at = (
        finished_datetime.isoformat()
    )

    metadata = create_run_metadata(
        input_snapshot=latest_snapshot,
        output_snapshot=output_snapshot,
        dataset_results=dataset_results,
        started_at=started_at,
        finished_at=finished_at
    )

    metadata_file = (
        output_snapshot /
        "metadata.json"
    )

    metadata_file.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    # TAG: FINAL RESULTS

    total_input = (
        metadata["records"]["input"]
    )

    total_output = (
        metadata["records"]["output"]
    )

    total_duplicates = (
        metadata["records"]["duplicates_removed"]
    )

    total_exact = (
        metadata["records"]["exact_duplicates_removed"]
    )

    total_content = (
        metadata["records"]["content_duplicates_removed"]
    )

    total_unidentifiable = (
        metadata["records"]["skipped_no_identity_signals"]
    )

    successful = (
        metadata["datasets"]["successful"]
    )

    failed = (
        metadata["datasets"]["failed"]
    )

    print("================================")
    print(" DEDUPLICATOR V1.2 FINISHED")
    print("================================")
    print()

    print(
        f"[RESULT] Datasets: "
        f"{len(dataset_results)}"
    )

    print(
        f"[RESULT] Successful: "
        f"{successful}"
    )

    print(
        f"[RESULT] Failed: "
        f"{failed}"
    )

    print()

    print(
        f"[RESULT] Input records: "
        f"{format_number(total_input)}"
    )

    print(
        f"[RESULT] Output records: "
        f"{format_number(total_output)}"
    )

    print(
        f"[RESULT] Duplicates removed: "
        f"{format_number(total_duplicates)}"
    )

    print(
        f"[RESULT]   exact (byte-identical): "
        f"{format_number(total_exact)}"
    )

    print(
        f"[RESULT]   content (same object, different source): "
        f"{format_number(total_content)}"
    )

    print(
        f"[RESULT] Skipped, no identity signals: "
        f"{format_number(total_unidentifiable)}"
    )

    print()

    print("[INFO] Output:")
    print(output_snapshot)

    print()

    print("[INFO] Metadata:")
    print(metadata_file)

    print()

    if failed > 0:

        print(
            "[WARNING] Some datasets failed."
        )

        for result in dataset_results:

            if result["status"] == "FAILED":

                print(
                    f"  - "
                    f"{result['source']}/"
                    f"{result['dataset_id']}"
                )

                print(
                    f"    {result.get('error')}"
                )

        return 1

    print(
        "[OK] All datasets processed successfully."
    )

    return 0


# TAG: TEST DATA

def create_test_dataset(
    test_dir: Path
) -> Path:

    test_file = (
        test_dir /
        "dataset_test.json"
    )

    test_records = [
        {
            "id": 1,
            "name": "Test Property A",
            "attributes": {
                "area": 100
            }
        },
        {
            "id": 2,
            "name": "Test Property B",
            "attributes": {
                "area": 200
            }
        },
        {
            "id": 1,
            "name": "Test Property A",
            "attributes": {
                "area": 100
            }
        },
        {
            "id": 3,
            "name": "Test Property C",
            "attributes": {
                "area": 300
            }
        },
        {
            "id": 2,
            "name": "Test Property B",
            "attributes": {
                "area": 200
            }
        }
    ]

    test_file.write_text(
        json.dumps(
            test_records,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return test_file


# TAG: TEST VALIDATION

def validate_test_output(
    output_file: Path
) -> tuple[int, int]:

    records = list(
        read_normalized_records(
            output_file
        )
    )

    return (
        len(records),
        len({
            calculate_fingerprint(record)
            for record in records
        })
    )


# TAG: AUTOMATED TEST

def run_test() -> int:

    print()
    print("================================")
    print(" DEDUPLICATOR AUTOMATED TEST")
    print("================================")
    print()

    start_time = time.perf_counter()

    # TAG: TEMP TEST DIRECTORY

    with tempfile.TemporaryDirectory(
        prefix="deduplicator_test_"
    ) as temporary_directory:

        test_dir = Path(
            temporary_directory
        )

        input_file = create_test_dataset(
            test_dir
        )

        output_file = (
            test_dir /
            "deduplicated.json"
        )

        print(
            "[INFO] Test input:"
        )

        print(input_file)

        print()

        print(
            "[INFO] Expected:"
        )

        print("      Input records: 5")
        print("      Output records: 3")
        print("      Duplicates: 2")

        print()

        # TAG: RUN REAL ALGORITHM

        result = process_dataset(
            input_file,
            output_file
        )

        print(
            "[INFO] Actual:"
        )

        print(
            f"      Input records: "
            f"{result['input_records']}"
        )

        print(
            f"      Output records: "
            f"{result['output_records']}"
        )

        print(
            f"      Duplicates: "
            f"{result['duplicate_records']}"
        )

        print()

        # TAG: VALIDATE COUNTS

        if result["input_records"] != 5:

            print(
                "[TEST FAILED] "
                "Input record count is not 5."
            )

            return 1

        if result["output_records"] != 3:

            print(
                "[TEST FAILED] "
                "Output record count is not 3."
            )

            return 1

        if result["duplicate_records"] != 2:

            print(
                "[TEST FAILED] "
                "Duplicate count is not 2."
            )

            return 1

        # TAG: VALIDATE OUTPUT JSON

        if not output_file.exists():

            print(
                "[TEST FAILED] "
                "Output file was not created."
            )

            return 1

        output_count, unique_count = (
            validate_test_output(
                output_file
            )
        )

        if output_count != 3:

            print(
                "[TEST FAILED] "
                "Output JSON contains "
                f"{output_count} records instead of 3."
            )

            return 1

        if unique_count != 3:

            print(
                "[TEST FAILED] "
                "Output still contains duplicates."
            )

            return 1

        elapsed = (
            time.perf_counter() - start_time
        )

        print(
            "[OK] Input count validated."
        )

        print(
            "[OK] Output count validated."
        )

        print(
            "[OK] Duplicate count validated."
        )

        print(
            "[OK] Output JSON validated."
        )

        print()

        print("================================")
        print("[TEST PASSED]")
        print("================================")

        print()

        print(
            f"[INFO] Test time: "
            f"{format_duration(elapsed)}"
        )

        print(
            "[INFO] Temporary test files "
            "were automatically removed."
        )

        print()

    return 0


# TAG: CLI

def print_usage():

    print()
    print("Usage:")
    print()
    print(
        "  python tools\\deduplicator.py"
    )

    print()

    print(
        "Automated test:"
    )

    print(
        "  python tools\\deduplicator.py --test"
    )

    print()


# TAG: ENTRY POINT

def main():

    if len(sys.argv) == 1:

        exit_code = run_deduplication()

        sys.exit(exit_code)

    if len(sys.argv) == 2:

        if sys.argv[1] == "--test":

            exit_code = run_test()

            sys.exit(exit_code)

    print_usage()

    sys.exit(1)


if __name__ == "__main__":
    main()

