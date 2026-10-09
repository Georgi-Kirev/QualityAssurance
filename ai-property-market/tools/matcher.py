# TAG: MATCHER
# Универсален Matcher за откриване на записи,
# които вероятно описват един и същ реален обект.
#
# V2.3
#
# Основни принципи:
# - Source ID НЕ е Global ID.
# - Липсваща информация НЕ е конфликт.
# - Еднакъв надежден Global ID е силен сигнал.
# - Area се нормализира до m².
# - Address се нормализира.
# - SQLite се използва само като rebuildable index.
# - RAW / NORMALIZED / DEDUPLICATED data не се променят.
# - Matcher работи върху последния DEDUPLICATED snapshot.
#
# V2.3 технически подобрения:
# - По-безопасно откриване на dataset файлове.
# - Source се извлича от структурата на snapshot-а.
# - Няма огромен in-memory seen_pairs set.
# - Намален брой SQLite заявки при matching.
# - SQLite performance настройки.
# - Batch commit при index build.
# - X/Y не се приемат автоматично за GPS координати.
# - Премахнат мъртъв код.
# - Допълнителни автоматични тестове.


import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


# ============================================================
# TAG: VERSION
# ============================================================

VERSION = "2.3"
SCHEMA_VERSION = "2.3"


# ============================================================
# TAG: PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

NORMALIZED_DIR = BASE_DIR / "storage" / "normalized"
DEDUPLICATED_DIR = BASE_DIR / "storage" / "deduplicated"
MATCHED_DIR = BASE_DIR / "storage" / "matched"
INDEX_DIR = BASE_DIR / "storage" / "indexes"

DEFAULT_DB = INDEX_DIR / "matcher.db"


# ============================================================
# TAG: MATCH WEIGHTS
# ============================================================

FIELD_WEIGHTS = {
    "global_identifier": 40.0,
    "address": 20.0,
    "coordinates": 15.0,
    "area_m2": 10.0,
    "property_type": 5.0,
    "building_area_m2": 4.0,
    "building_floors": 2.0,
    "apartment_floor": 2.0,
    "rooms": 1.0,
    "name": 1.0,
}


# ============================================================
# TAG: THRESHOLDS
# ============================================================

MATCH_THRESHOLD = 75.0
POSSIBLE_THRESHOLD = 45.0
RELATED_THRESHOLD = 45.0

# Минимум сравними тежести, без които изобщо не се
# допуска MATCH. global_identifier = 40, address = 20,
# coordinates = 15, area_m2 = 10.
# Тоест MATCH изисква поне global_id ИЛИ address+coordinates
# ИЛИ address+area. Съгласие само по координати (15) или
# само по площ (10) не е достатъчно - иначе споделен връх
# на геометрия се обявява за една и съща обект.
MIN_WEIGHT_FOR_MATCH = 30.0

COORDINATE_EXACT_TOLERANCE = 0.000001
COORDINATE_CLOSE_TOLERANCE = 0.001

AREA_RELATIVE_TOLERANCE = 0.05

MAX_CANDIDATES = 500

INDEX_COMMIT_BATCH = 25000


# ============================================================
# TAG: FIELD KEY NORMALIZATION
# ============================================================

def normalize_field_key(value: Any) -> str:
    """
    Превръща различни варианти на име на поле
    в един и същ вътрешен ключ.

    Примери:

        cadastral_id
        cadastral-id
        cadastral id
        Cadastral ID

    -> cadastralid
    """

    if value is None:
        return ""

    text = str(value).strip().lower()

    text = text.replace("²", "2")

    text = re.sub(
        r"[^0-9a-zа-я]+",
        "",
        text
    )

    return text


# ============================================================
# TAG: TEXT NORMALIZATION
# ============================================================

CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sht",
    "ъ": "a",
    "ь": "",
    "ю": "yu",
    "я": "ya",
})


def normalize_text(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip().lower()

    text = text.replace("№", " no ")
    text = text.replace("–", "-")
    text = text.replace("—", "-")

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text


def transliterate_text(value: Any) -> str:

    text = normalize_text(value)

    return text.translate(
        CYRILLIC_TO_LATIN
    )


# ============================================================
# TAG: ADDRESS NORMALIZATION
# ============================================================

ADDRESS_REPLACEMENTS = {
    "бул": "булевард",
    "ул": "улица",
    "жк": "жилищенкомплекс",
    "кв": "квартал",
    "бл": "блок",
    "вх": "вход",
    "ет": "етаж",
    "ап": "апартамент",
}


def normalize_address(value: Any) -> str:
    """
    Нормализира адреси без да унищожава важни номера.

    Например:

        бул. България 10
        бул България №10

    -> булевард българия 10

    Номерът на апартамент/блок/етаж остава значим.
    """

    if value is None:
        return ""

    text = normalize_text(value)

    text = re.sub(
        r"[,.;:/\\()\[\]{}]",
        " ",
        text
    )

    text = re.sub(
        r"\bno\s+",
        "no ",
        text
    )

    tokens = text.split()

    result = []

    for token in tokens:

        replacement = ADDRESS_REPLACEMENTS.get(
            token,
            token
        )

        result.append(
            replacement
        )

    cleaned = []

    i = 0

    while i < len(result):

        token = result[i]

        if token == "no":

            if (
                i + 1 < len(result)
                and re.fullmatch(
                    r"\d+[a-zа-я]?",
                    result[i + 1]
                )
            ):
                i += 1
                continue

        cleaned.append(token)

        i += 1

    text = " ".join(
        cleaned
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


# ============================================================
# TAG: GLOBAL ID FIELDS
# ============================================================

GLOBAL_ID_FIELD_NAMES = {
    "upi",
    "pi",
    "cadastralid",
    "cadastralidentifier",
    "cadastralnumber",
    "cadastralno",
    "cadastreid",
    "globalid",
    "globalidentifier",
}


SOURCE_ID_FIELD_NAMES = {
    "id",
    "sourceid",
    "recordid",
    "objectid",
    "fid",
    "gid",
    "pk",
}


# ============================================================
# TAG: AREA FIELD DETECTION
# ============================================================

AREA_UNIT_FACTORS = {
    "m2": 1.0,
    "m": 1.0,
    "sqm": 1.0,
    "sqmeter": 1.0,
    "squaremeter": 1.0,

    "ar": 100.0,
    "are": 100.0,
    "ares": 100.0,

    "dka": 1000.0,
    "decare": 1000.0,
    "decares": 1000.0,

    "ha": 10000.0,
    "hectare": 10000.0,
    "hectares": 10000.0,

    # --------------------------------------------------------
    # km2 ДОБАВЕН на 28.09.2026.
    #
    # Преди го нямаше. После "area_km2" падаше в клона
    # key.endswith("m2") -> "m2" -> множител 1.0, т.е.
    # 2.4 km2 се записваше като 2.4 m2. Грешка x1 000 000.
    #
    # Измерено на реални данни: dataset 604, 564 записа,
    # всички с area_km2. Средното в finished metadata беше
    # 2.4 "m2" - тоест 2.4 km2, прочетени като 2.4 m2.
    #
    # Защо не беше забелязано: стойността 2.4 е правдоподобна
    # и като m2, така че никой тест не я е бил длъжен да
    # отрече. Грешката се вижда само при мащаба.
    # --------------------------------------------------------
    "km2": 1000000.0,
    "sqkm": 1000000.0,
    "squarekilometer": 1000000.0,
    "squarekilometers": 1000000.0,
}


def normalize_unit(
    value: Any
) -> Optional[str]:

    if value is None:
        return None

    text = normalize_field_key(
        value
    )

    aliases = {
        "m2": "m2",
        "m": "m2",
        "sqm": "m2",
        "squaremeter": "m2",
        "squaremeters": "m2",

        "ar": "ar",
        "are": "ar",
        "ares": "ar",

        "dka": "dka",
        "decare": "dka",
        "decares": "dka",

        "ha": "ha",
        "hectare": "ha",
        "hectares": "ha",

        # km2 - виж коментара при AREA_UNIT_FACTORS.
        # Без този ред "km2" минаваше през "m2" (endswith)
        # и се умножаваше по 1 вместо по 1 000 000.
        "km2": "km2",
        "sqkm": "km2",
        "squarekilometer": "km2",
        "squarekilometers": "km2",
    }

    return aliases.get(
        text
    )


def area_to_m2(
    value: Any,
    unit: Any
) -> Optional[float]:

    if value is None:
        return None

    normalized_unit = normalize_unit(
        unit
    )

    if normalized_unit is None:
        return None

    try:

        number = float(value)

    except (
        TypeError,
        ValueError
    ):

        return None

    return (
        number
        * AREA_UNIT_FACTORS[
            normalized_unit
        ]
    )


def detect_area_unit_from_field(
    field_name: str
) -> Optional[str]:

    key = normalize_field_key(
        field_name
    )

    if not key:

        return None

    # --------------------------------------------------------
    # 0. Български алиаси за квадратен метър.
    #    Трябва да са ПРЕДИ "are"/"ar", иначе "areakvm"
    #    приключва с "ar" (x100) вместо "m2" (x1).
    # --------------------------------------------------------

    if (
        key.endswith("kvm") or
        key.endswith("kvadratm") or
        key.endswith("kvadratnim") or
        key.endswith("kvadratmetar") or
        key.endswith("ploshtm2")
    ):

        return "m2"

    # --------------------------------------------------------
    # 0-bis. КВАДРАТЕН КИЛОМЕТЪР - ПРЕДИ "m2".
    #
    # "areakm2" ЗАВЪРШВА на "m2". Без този блок общият цикъл
    # надолу го връща като "m2" (множител 1) и 2.4 km2
    # става 2.4 m2. Грешка x1 000 000, измерена на dataset 604.
    #
    # Проверката е за ЦЯЛ токен, не за substring: иначе
    # името "б km2" трябва да мине, но "нещоkm2x" - не.
    # --------------------------------------------------------

    km2_markers = (
        "km2",
        "sqkm",
        "squarekilometer",
        "squarekilometers",
    )

    raw_km2_tokens = set(
        re.split(
            r"[^0-9a-zA-Z]+",
            str(
                field_name
            ).strip().lower(),
        )
    )

    for marker in km2_markers:

        if (
            key.endswith(marker)
            or marker in raw_km2_tokens
        ):

            return "km2"

    # normalize_field_key() маха разделителите, затова
    # за "само цял сегмент" ползваме оригиналното име,
    # разбито на токени.

    raw_tokens = set(
        re.split(
            r"[^0-9a-zA-Z]+",
            str(
                field_name
            ).strip().lower()
        )
    )

    # "area_m" / "plosht_m" / "surface_m" -> квадратни метри.
    # Само ако последният токен е точно "m" и името
    # вече е разпознато като area поле.

    if "m" in raw_tokens and is_area_field(
        field_name
    ):

        return "m2"

    for unit in (
        "hectares",
        "hectare",
        "dka",
        "decares",
        "decare",
        "ares",
        "are",
        "m2",
        "sqm",
        "ha",
        "ar",
    ):

        normalized_unit = normalize_unit(
            unit
        )

        if normalized_unit is None:
            continue

        unit_key = normalize_field_key(
            unit
        )

        if unit_key in {
            "are",
            "ha",
        }:

            # Само цял сегмент - иначе "share" -> "ar",
            # "shape"/"phases" -> "ha".

            if unit_key not in raw_tokens:
                continue

        if key.endswith(
            unit_key
        ):

            return normalized_unit

    return None


def is_area_field(
    field_name: str
) -> bool:

    key = normalize_field_key(
        field_name
    )

    if "area" in key:
        return True

    if "surface" in key:
        return True

    if "plosht" in key:
        return True

    if "squaremeter" in key:
        return True

    if "kvadratm" in key:
        return True

    if "sqm" in key:
        return True

    if "kvm" in key:
        return True

    return False


def extract_area_m2(
    record: Dict[str, Any]
) -> Optional[float]:
    """
    Намира area стойност само когато
    има надеждна информация за единицата.

    Голо число без unit не се приема.
    """

    normalized_fields = {
        normalize_field_key(k): (k, v)
        for k, v in record.items()
    }

    # --------------------------------------------------------
    # 1. Unit директно в името на полето.
    # --------------------------------------------------------

    for (
        normalized_key,
        (original_key, value)
    ) in normalized_fields.items():

        if not is_area_field(
            original_key
        ):
            continue

        detected_unit = (
            detect_area_unit_from_field(
                original_key
            )
        )

        if detected_unit:

            result = area_to_m2(
                value,
                detected_unit
            )

            if result is not None:
                return result

    # --------------------------------------------------------
    # 2. area + area_unit
    # --------------------------------------------------------

    area_value = None

    for (
        normalized_key,
        (original_key, value)
    ) in normalized_fields.items():

        if normalized_key == "area":

            area_value = value

            break

    if area_value is not None:

        unit_value = None

        for (
            normalized_key,
            (original_key, value)
        ) in normalized_fields.items():

            if normalized_key in {
                "areaunit",
                "unitarea",
                "areaunits",
            }:

                unit_value = value

                break

        result = area_to_m2(
            area_value,
            unit_value
        )

        if result is not None:
            return result

    return None


# ============================================================
# TAG: NUMBER EXTRACTION
# ============================================================

def to_float(
    value: Any
) -> Optional[float]:

    if value is None:
        return None

    try:
        return float(value)

    except (
        TypeError,
        ValueError
    ):

        return None


# ============================================================
# TAG: GLOBAL ID EXTRACTION
# ============================================================

def extract_global_identifiers(
    record: Dict[str, Any]
) -> List[str]:

    identifiers = []

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in SOURCE_ID_FIELD_NAMES:
            continue

        if key not in GLOBAL_ID_FIELD_NAMES:
            continue

        if value is None:
            continue

        if isinstance(
            value,
            (dict, list)
        ):
            continue

        normalized_value = normalize_text(
            value
        )

        if not normalized_value:
            continue

        identifiers.append(
            normalized_value
        )

    return sorted(
        set(identifiers)
    )


# ============================================================
# TAG: ADDRESS EXTRACTION
# ============================================================

# TAG: MEANINGFUL ADDRESS
# Стойности, които не носят информация и не бива да се
# третират като адрес за entity matching. Ако не се филтрат,
# всички записи с "n/a" се матчват помежду си на 100%.
#
# Ключовете са в "compact" форма (без разделители), защото
# проверката по-долу сравнява точно така.

_PLACEHOLDER_ADDRESSES = {
    "",
    "na",
    "nan",
    "none",
    "null",
    "nil",
    "undefined",
    "unknown",
    "noaddress",
    "noadr",
    "address",
    "location",
    "latlon",
    "point",
    "geometry",
    "coordinates",
    "featurecollection",
    "feature",
    # Български
    "безадрес",
    "няма",
    "нема",
    "липсва",
    "неизвестен",
    "неизвестна",
    "нямаданни",
    "празно",
    "нула",
}


def is_meaningful_address(
    value: str
) -> bool:

    if not isinstance(
        value,
        str,
    ):
        return False

    candidate = value.strip()

    if not candidate:
        return False

    # Проверка без разделители - "no address" и "noaddress"
    # трябва да се отхвърлят еднакво.
    compact = re.sub(
        r"[^0-9a-z\u0410-\u042f\u0430-\u044f]+",
        "",
        candidate.lower()
    )

    if compact in _PLACEHOLDER_ADDRESSES:
        return False

    # Трябва да има поне една буква или цифра.
    if not re.search(
        r"[0-9\u0410-\u042f\u0430-\u044fA-Za-z]",
        candidate
    ):
        return False

    # Минимум 3 букви/цифри. "5" / "N" / "-" са твърде слаби
    # сигнали и не бива да носят тежест при matching.
    if len(compact) < 3:
        return False

    return True


def extract_address(
    record: Dict[str, Any]
) -> str:

    # "location" НЕ е адрес - в схемата на SofiaPlan това е
    # контейнер за геометрия. Ако се приеме за адрес, геометрията
    # се stringify-ва и две записа със СЪЩАТА геометрия получават
    # "адрес" match със score 100. Това предизвикваше 25 825
    # фалшиви MATCH-а върху 8 242 записа.

    preferred_fields = {
        "address",
        "fulladdress",
        "streetaddress",
        "addressfull",
        "adres",
    }

    for field_name, value in record.items():

        # Адресът е ТЕКСТ. Число в поле "address" е
        # всъщност идентификатор - иначе записи с еднакъв
        # object_id стават MATCH.

        if not isinstance(
            value,
            str
        ):
            continue

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = normalize_address(
                value
            )

            if is_meaningful_address(
                result
            ):
                return result

    return ""


# ============================================================
# TAG: NAME EXTRACTION
# ============================================================

def extract_name(
    record: Dict[str, Any]
) -> str:

    preferred_fields = {
        "name",
        "title",
        "objectname",
        "propertyname",
    }

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = normalize_text(
                value
            )

            if result:
                return result

    return ""


# ============================================================
# TAG: PROPERTY TYPE
# ============================================================

def extract_property_type(
    record: Dict[str, Any]
) -> str:

    preferred_fields = {
        "propertytype",
        "type",
        "category",
        "kind",
        "objecttype",
    }

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = normalize_text(
                value
            )

            if result:
                return result

    return ""


# ============================================================
# TAG: BUILDING FLOORS
# ============================================================

def extract_building_floors(
    record: Dict[str, Any]
) -> Optional[float]:

    preferred_fields = {
        "buildingfloors",
        "floors",
        "floorcount",
        "numberoffloors",
        "etazhi",
        "etaji",
    }

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = to_float(
                value
            )

            if result is not None:
                return result

    return None


# ============================================================
# TAG: APARTMENT FLOOR
# ============================================================

def extract_apartment_floor(
    record: Dict[str, Any]
) -> Optional[float]:

    preferred_fields = {
        "apartmentfloor",
        "floor",
        "etazh",
        "etaj",
    }

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = to_float(
                value
            )

            if result is not None:
                return result

    return None


# ============================================================
# TAG: ROOMS
# ============================================================

def extract_rooms(
    record: Dict[str, Any]
) -> Optional[float]:

    preferred_fields = {
        "rooms",
        "roomcount",
        "numberofrooms",
    }

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in preferred_fields:

            result = to_float(
                value
            )

            if result is not None:
                return result

    return None


# ============================================================
# TAG: BUILDING AREA
# ============================================================

def extract_building_area_m2(
    record: Dict[str, Any]
) -> Optional[float]:

    normalized_fields = {
        normalize_field_key(k): (k, v)
        for k, v in record.items()
    }

    # --------------------------------------------------------
    # 1. Building area with unit in field name.
    #
    # Examples:
    # building_area_m2
    # buildingarea_m2
    # building_area1970ha
    # --------------------------------------------------------

    for (
        normalized_key,
        (original_key, value)
    ) in normalized_fields.items():

        if "building" not in normalized_key:
            continue

        if not is_area_field(
            original_key
        ):
            continue

        detected_unit = (
            detect_area_unit_from_field(
                original_key
            )
        )

        if detected_unit:

            result = area_to_m2(
                value,
                detected_unit
            )

            if result is not None:
                return result

    # --------------------------------------------------------
    # 2. Companion fields.
    #
    # building_area + building_area_unit
    # --------------------------------------------------------

    building_area_value = None
    building_area_unit = None

    for (
        normalized_key,
        (original_key, value)
    ) in normalized_fields.items():

        if normalized_key == "buildingarea":

            building_area_value = value

        elif normalized_key in {
            "buildingareaunit",
            "unitbuildingarea",
        }:

            building_area_unit = value

    if (
        building_area_value is not None
        and building_area_unit is not None
    ):

        return area_to_m2(
            building_area_value,
            building_area_unit
        )

    return None


# ============================================================
# TAG: COORDINATES
# ============================================================

def first_geojson_coordinate(
    node: Any
) -> Optional[Tuple[float, float]]:
    """
    Върща първата двойка [longitude, latitude] от
    произволно вложена GeoJSON координатна структура.

    GeoJSON спецификацията е [lon, lat] - редът е
    ЗАДЪЛЖИТЕЛЕН и не бива да се обръща.
    """

    if not isinstance(
        node,
        (list, tuple)
    ):
        return None

    if not node:
        return None

    first = node[0]

    # [lon, lat]
    if (
        isinstance(
            first,
            (int, float),
        )
        and not isinstance(
            first,
            bool,
        )
        and len(node) >= 2
        and isinstance(
            node[1],
            (int, float),
        )
        and not isinstance(
            node[1],
            bool,
        )
    ):

        lon = float(
            first
        )
        lat = float(
            node[1]
        )

        if (
            -90.0 <= lat <= 90.0
            and -180.0 <= lon <= 180.0
        ):
            return lat, lon

        return None

    # Вложена структура - рекурсия.
    return first_geojson_coordinate(
        first
    )


def extract_geometry_coordinates(
    record: Dict[str, Any]
) -> Optional[Tuple[float, float]]:
    """
    Всички записи в SofiaPlan носят GeoJSON геометрия
    (location.geometry). Без нея matcher-ът няма
    blocking ключ по координати и не може да намери
    кандидати изобщо.
    """

    candidates = []

    location = record.get(
        "location"
    )

    if isinstance(
        location,
        dict,
    ):
        candidates.append(
            location.get(
                "geometry"
            )
        )

    candidates.append(
        record.get(
            "geometry"
        )
    )

    for geometry in candidates:

        if not isinstance(
            geometry,
            dict,
        ):
            continue

        result = first_geojson_coordinate(
            geometry.get(
                "coordinates"
            )
        )

        if result is not None:
            return result

    return None


def extract_coordinates(
    record: Dict[str, Any]
) -> Optional[Tuple[float, float]]:

    lat = None
    lon = None

    for field_name, value in record.items():

        key = normalize_field_key(
            field_name
        )

        if key in {
            "lat",
            "latitude",
        }:

            lat = to_float(
                value
            )

        elif key in {
            "lon",
            "lng",
            "longitude",
        }:

            lon = to_float(
                value
            )

    if lat is not None and lon is not None:

        # Basic sanity check for geographic coordinates.
        if (
            -90.0 <= lat <= 90.0
            and -180.0 <= lon <= 180.0
        ):

            return lat, lon

        return None

    # Fallback: GeoJSON геометрия в location.geometry.
    return extract_geometry_coordinates(
        record
    )


# ============================================================
# TAG: FEATURE EXTRACTION
# ============================================================

def extract_features(
    record: Dict[str, Any]
) -> Dict[str, Any]:

    return {
        "global_identifiers":
            extract_global_identifiers(record),

        "address":
            extract_address(record),

        "name":
            extract_name(record),

        "property_type":
            extract_property_type(record),

        "area_m2":
            extract_area_m2(record),

        "building_area_m2":
            extract_building_area_m2(record),

        "building_floors":
            extract_building_floors(record),

        "apartment_floor":
            extract_apartment_floor(record),

        "rooms":
            extract_rooms(record),

        "coordinates":
            extract_coordinates(record),
    }


# ============================================================
# TAG: TOKEN SIMILARITY
# ============================================================

def token_jaccard(
    a: str,
    b: str
) -> float:

    if not a or not b:
        return 0.0

    set_a = set(
        a.split()
    )

    set_b = set(
        b.split()
    )

    if not set_a or not set_b:
        return 0.0

    intersection = len(
        set_a & set_b
    )

    union = len(
        set_a | set_b
    )

    if union == 0:
        return 0.0

    return intersection / union


# ============================================================
# TAG: COORDINATE COMPARISON
# ============================================================

def compare_coordinates(
    a: Optional[Tuple[float, float]],
    b: Optional[Tuple[float, float]]
) -> Optional[Tuple[float, str]]:

    if a is None or b is None:
        return None

    lat_diff = abs(
        a[0] - b[0]
    )

    lon_diff = abs(
        a[1] - b[1]
    )

    if (
        lat_diff
        <= COORDINATE_EXACT_TOLERANCE
        and
        lon_diff
        <= COORDINATE_EXACT_TOLERANCE
    ):

        return 1.0, "coordinates_exact"

    if (
        lat_diff
        <= COORDINATE_CLOSE_TOLERANCE
        and
        lon_diff
        <= COORDINATE_CLOSE_TOLERANCE
    ):

        return 0.7, "coordinates_close"

    return 0.0, "coordinates_different"


# ============================================================
# TAG: AREA COMPARISON
# ============================================================

def compare_area(
    a: Optional[float],
    b: Optional[float]
) -> Optional[Tuple[float, str]]:

    if a is None or b is None:
        return None

    if a == 0 or b == 0:

        return (
            (1.0, "area_match")
            if a == b
            else
            (0.0, "area_conflict")
        )

    relative_difference = (
        abs(a - b)
        / max(abs(a), abs(b))
    )

    if (
        relative_difference
        <= AREA_RELATIVE_TOLERANCE
    ):

        return 1.0, "area_match"

    return 0.0, "area_conflict"


# ============================================================
# TAG: NUMERIC COMPARISON
# ============================================================

def compare_numeric(
    a: Optional[float],
    b: Optional[float],
    signal_name: str
) -> Optional[Tuple[float, str]]:

    if a is None or b is None:
        return None

    if math.isclose(
        a,
        b,
        rel_tol=0.0,
        abs_tol=0.000001
    ):

        return 1.0, signal_name

    return (
        0.0,
        signal_name + "_conflict"
    )


# ============================================================
# TAG: GLOBAL ID RELATION
# ============================================================

def compare_global_ids(
    a: List[str],
    b: List[str]
) -> Optional[Tuple[float, str]]:

    if not a or not b:
        return None

    intersection = (
        set(a) & set(b)
    )

    if intersection:

        return (
            1.0,
            "global_identifier_match"
        )

    return (
        0.0,
        "global_identifier_conflict"
    )


# ============================================================
# TAG: MAIN FEATURE COMPARISON
# ============================================================

def compare_features(
    a: Dict[str, Any],
    b: Dict[str, Any]
) -> Dict[str, Any]:

    signals = []
    conflicts = []

    weighted_score = 0.0
    available_weight = 0.0

    # --------------------------------------------------------
    # Global identifier
    # --------------------------------------------------------

    global_result = compare_global_ids(
        a["global_identifiers"],
        b["global_identifiers"]
    )

    if global_result is not None:

        score, signal = global_result

        if score == 1.0:

            return {
                "status": "MATCH",
                "score": 100.0,
                "signals": [signal],
                "conflicts": [],
            }

        conflicts.append(
            signal
        )

        return {
            "status": "UNMATCHED",
            "score": 0.0,
            "signals": [],
            "conflicts": conflicts,
        }

    # --------------------------------------------------------
    # Address
    # --------------------------------------------------------

    if (
        a["address"]
        and b["address"]
    ):

        available_weight += (
            FIELD_WEIGHTS["address"]
        )

        if (
            a["address"]
            == b["address"]
        ):

            weighted_score += (
                FIELD_WEIGHTS["address"]
            )

            signals.append(
                "address_exact_or_normalized"
            )

        else:

            similarity = token_jaccard(
                a["address"],
                b["address"]
            )

            if similarity >= 0.75:

                weighted_score += (
                    FIELD_WEIGHTS["address"]
                    * similarity
                )

                signals.append(
                    "address_partial_match"
                )

            else:

                conflicts.append(
                    "address_different"
                )

    # --------------------------------------------------------
    # Coordinates
    # --------------------------------------------------------

    coordinate_result = (
        compare_coordinates(
            a["coordinates"],
            b["coordinates"]
        )
    )

    if coordinate_result is not None:

        score, signal = (
            coordinate_result
        )

        available_weight += (
            FIELD_WEIGHTS["coordinates"]
        )

        weighted_score += (
            FIELD_WEIGHTS["coordinates"]
            * score
        )

        if score > 0:

            signals.append(
                signal
            )

        else:

            conflicts.append(
                signal
            )

    # --------------------------------------------------------
    # Area
    # --------------------------------------------------------

    area_result = compare_area(
        a["area_m2"],
        b["area_m2"]
    )

    if area_result is not None:

        score, signal = area_result

        available_weight += (
            FIELD_WEIGHTS["area_m2"]
        )

        weighted_score += (
            FIELD_WEIGHTS["area_m2"]
            * score
        )

        if score > 0:

            signals.append(
                signal
            )

        else:

            conflicts.append(
                signal
            )

    # --------------------------------------------------------
    # Property type
    # --------------------------------------------------------

    if (
        a["property_type"]
        and b["property_type"]
    ):

        available_weight += (
            FIELD_WEIGHTS["property_type"]
        )

        if (
            a["property_type"]
            == b["property_type"]
        ):

            weighted_score += (
                FIELD_WEIGHTS["property_type"]
            )

            signals.append(
                "property_type_match"
            )

        else:

            conflicts.append(
                "property_type_conflict"
            )

    # --------------------------------------------------------
    # Building area
    # --------------------------------------------------------

    building_area_result = (
        compare_area(
            a["building_area_m2"],
            b["building_area_m2"]
        )
    )

    if building_area_result is not None:

        score, signal = (
            building_area_result
        )

        available_weight += (
            FIELD_WEIGHTS[
                "building_area_m2"
            ]
        )

        weighted_score += (
            FIELD_WEIGHTS[
                "building_area_m2"
            ]
            * score
        )

        if score > 0:

            signals.append(
                signal.replace(
                    "area_match",
                    "building_area_match"
                )
            )

        else:

            conflicts.append(
                "building_area_conflict"
            )

    # --------------------------------------------------------
    # Building floors
    # --------------------------------------------------------

    floors_result = compare_numeric(
        a["building_floors"],
        b["building_floors"],
        "building_floors_match"
    )

    if floors_result is not None:

        score, signal = floors_result

        available_weight += (
            FIELD_WEIGHTS[
                "building_floors"
            ]
        )

        weighted_score += (
            FIELD_WEIGHTS[
                "building_floors"
            ]
            * score
        )

        if score > 0:

            signals.append(
                signal
            )

        else:

            conflicts.append(
                "building_floors_conflict"
            )

    # --------------------------------------------------------
    # Apartment floor
    # --------------------------------------------------------

    apartment_floor_result = (
        compare_numeric(
            a["apartment_floor"],
            b["apartment_floor"],
            "apartment_floor_match"
        )
    )

    if apartment_floor_result is not None:

        score, signal = (
            apartment_floor_result
        )

        available_weight += (
            FIELD_WEIGHTS[
                "apartment_floor"
            ]
        )

        weighted_score += (
            FIELD_WEIGHTS[
                "apartment_floor"
            ]
            * score
        )

        if score > 0:

            signals.append(
                signal
            )

        else:

            conflicts.append(
                "apartment_floor_conflict"
            )

    # --------------------------------------------------------
    # Rooms
    # --------------------------------------------------------

    rooms_result = compare_numeric(
        a["rooms"],
        b["rooms"],
        "rooms_match"
    )

    if rooms_result is not None:

        score, signal = rooms_result

        available_weight += (
            FIELD_WEIGHTS["rooms"]
        )

        weighted_score += (
            FIELD_WEIGHTS["rooms"]
            * score
        )

        if score > 0:

            signals.append(
                signal
            )

        else:

            conflicts.append(
                "rooms_conflict"
            )

    # --------------------------------------------------------
    # Name
    # --------------------------------------------------------

    if (
        a["name"]
        and b["name"]
    ):

        available_weight += (
            FIELD_WEIGHTS["name"]
        )

        if (
            a["name"]
            == b["name"]
        ):

            weighted_score += (
                FIELD_WEIGHTS["name"]
            )

            signals.append(
                "name_exact_match"
            )

        else:

            similarity = token_jaccard(
                a["name"],
                b["name"]
            )

            if similarity >= 0.5:

                weighted_score += (
                    FIELD_WEIGHTS["name"]
                    * similarity
                )

                signals.append(
                    "name_partial_match"
                )

            else:

                conflicts.append(
                    "name_different"
                )

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    if available_weight == 0:

        return {
            "status": "UNMATCHED",
            "score": 0.0,
            "signals": [],
            "conflicts": [],
        }

    score = (
        weighted_score
        / available_weight
    ) * 100.0

    score = round(
        score,
        2
    )

    # --------------------------------------------------------
    # MINIMUM EVIDENCE GATE
    #
    # score е ОТНОШЕНИЕ (weighted/available). Ако единственото
    # сравнимо поле е напр. координати (15 точки), тогава
    # 15/15 = 100% и всеки двойка, която споделя връх на
    # геометрия, става MATCH. Това изтрива хиляди записи.
    #
    # Изискваме поне MIN_WEIGHT_FOR_MATCH точки РЕАЛНО
    # сравними тежести, преди изобщо да се допуска MATCH.
    # --------------------------------------------------------

    if (
        available_weight
        < MIN_WEIGHT_FOR_MATCH
    ):

        return {
            "status": "UNMATCHED",
            "score": score,
            "signals": signals,
            "conflicts": conflicts,
        }

    # --------------------------------------------------------
    # Hard conflicts
    # --------------------------------------------------------

    hard_conflicts = {
        "address_different",
        "coordinates_different",
        "area_conflict",
        "building_area_conflict",
        "building_floors_conflict",
        "apartment_floor_conflict",
        "rooms_conflict",
    }

    has_hard_conflict = bool(
        hard_conflicts
        & set(conflicts)
    )

    # --------------------------------------------------------
    # RELATED
    # --------------------------------------------------------

    type_a = a["property_type"]
    type_b = b["property_type"]

    related_keywords = {
        "parcel",
        "земли",
        "land",
        "plot",
        "upi",
        "pi",
        "building",
        "сграда",
    }

    is_related_type_pair = (
        type_a
        and type_b
        and type_a != type_b
        and any(
            keyword in type_a
            for keyword in related_keywords
        )
        and any(
            keyword in type_b
            for keyword in related_keywords
        )
    )

    if (
        is_related_type_pair
        and score >= RELATED_THRESHOLD
    ):

        return {
            "status": "RELATED",
            "score": score,
            "signals": signals,
            "conflicts": conflicts,
        }

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    if (
        not has_hard_conflict
        and score >= MATCH_THRESHOLD
    ):

        return {
            "status": "MATCH",
            "score": score,
            "signals": signals,
            "conflicts": conflicts,
        }

    # --------------------------------------------------------
    # POSSIBLE
    # --------------------------------------------------------

    if (
        not has_hard_conflict
        and score >= POSSIBLE_THRESHOLD
    ):

        return {
            "status": "POSSIBLE",
            "score": score,
            "signals": signals,
            "conflicts": conflicts,
        }

    return {
        "status": "UNMATCHED",
        "score": score,
        "signals": signals,
        "conflicts": conflicts,
    }


# ============================================================
# TAG: RECORD FINGERPRINT
# ============================================================

def record_fingerprint(
    record: Dict[str, Any]
) -> str:

    canonical = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


# ============================================================
# TAG: JSON ITERATOR
# ============================================================

def iter_json_records(
    path: Path
) -> Iterator[Dict[str, Any]]:

    import ijson

    # --------------------------------------------------------
    # GeoJSON FeatureCollection
    # --------------------------------------------------------

    try:

        with path.open(
            "rb"
        ) as handle:

            first_items = ijson.items(
                handle,
                "features.item",
                use_float=True
            )

            found = False

            for item in first_items:

                found = True

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                if (
                    item.get("type")
                    == "Feature"
                    and
                    isinstance(
                        item.get("properties"),
                        dict
                    )
                ):

                    record = dict(
                        item["properties"]
                    )

                    if "geometry" in item:

                        record["geometry"] = (
                            item["geometry"]
                        )

                    yield record

                else:

                    yield item

            if found:
                return

    except Exception:
        pass

    # --------------------------------------------------------
    # JSON array
    # --------------------------------------------------------

    try:

        with path.open(
            "rb"
        ) as handle:

            for item in ijson.items(
                handle,
                "item",
                use_float=True
            ):

                if isinstance(
                    item,
                    dict
                ):

                    yield item

        return

    except Exception:
        pass

    # --------------------------------------------------------
    # Single JSON object
    # --------------------------------------------------------

    try:

        with path.open(
            "rb"
        ) as handle:

            data = json.load(
                handle
            )

            if isinstance(
                data,
                dict
            ):

                yield data

    except Exception as error:

        raise RuntimeError(
            f"Unable to parse JSON dataset: "
            f"{path} | {error}"
        )


# ============================================================
# TAG: DATASET FILE DISCOVERY
# ============================================================

SUPPORTED_DATASET_NAMES = {
    "normalized.json",
    "data.json",
    "records.json",
    "data.geojson",
    "deduplicated.json",
}


def find_dataset_files(
    snapshot_dir: Path
) -> List[Path]:

    files = []

    for path in snapshot_dir.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in {
            ".json",
            ".geojson",
        }:

            continue

        # Metadata and other control files
        # are not datasets.
        if path.name.lower() in {
            "metadata.json",
            "manifest.json",
            "schema.json",
        }:

            continue

        # If the filename follows a known dataset
        # naming convention, accept it.
        if path.name.lower() in {
            name.lower()
            for name in SUPPORTED_DATASET_NAMES
        }:

            files.append(path)
            continue

        # Otherwise accept only JSON files that
        # live inside a dataset-like directory.
        parent_name = (
            path.parent.name.lower()
        )

        if parent_name not in {
            "metadata",
            "cache",
            "discovery",
            "indexes",
        }:

            files.append(path)

    return sorted(
        set(files),
        key=lambda p: str(p)
    )


# ============================================================
# TAG: LATEST SNAPSHOT
# ============================================================

def find_latest_snapshot(
    root_dir: Path
) -> Optional[Path]:

    if not root_dir.exists():
        return None

    directories = [
        path
        for path in root_dir.iterdir()
        if path.is_dir()
    ]

    if not directories:
        return None

    return max(
        directories,
        key=lambda p: p.stat().st_mtime
    )


# TAG: DATASET IDENTIFICATION
# Разпознава source и dataset_id от реалната структура на snapshot файловете.

def identify_dataset(snapshot_dir: Path, file_path: Path) -> Tuple[str, str]:
    """
    Определя source и dataset_id от пътя на dataset файла.

    Поддържани структури:

    1. snapshot/source/dataset_id/data.json
    2. snapshot/source/dataset_id/normalized.json
    3. snapshot/source/dataset_123.json

    Пример:
        snapshot/sofiaplan/dataset_626.json
        -> ("sofiaplan", "626")
    """

    try:
        relative = file_path.relative_to(snapshot_dir)
    except ValueError:
        return "unknown", "unknown"

    parts = relative.parts

    if len(parts) < 2:
        return "unknown", "unknown"

    source = parts[0]

    # TAG: DATASET FILE FORMAT
    # Реалната production структура е:
    # source/dataset_123.json
    filename = file_path.name

    match = re.match(r"^dataset_(\d+)\.(json|geojson)$", filename, re.IGNORECASE)

    if match:
        dataset_id = match.group(1)
        return source, dataset_id

    # TAG: DIRECTORY DATASET FORMAT
    # Поддръжка на алтернативна структура:
    # source/123/data.json
    # source/123/normalized.json

    if len(parts) >= 3:
        dataset_dir = parts[-2]
        data_filename = parts[-1].lower()

        if (
            dataset_dir.isdigit()
            and data_filename in {
                "data.json",
                "normalized.json",
                "dataset.json",
            }
        ):
            return source, dataset_dir

    return "unknown", file_path.stem


# ============================================================
# TAG: SQLITE PERFORMANCE
# ============================================================

def configure_database(
    connection: sqlite3.Connection
) -> None:

    connection.execute(
        "PRAGMA journal_mode = WAL"
    )

    connection.execute(
        "PRAGMA synchronous = NORMAL"
    )

    connection.execute(
        "PRAGMA temp_store = MEMORY"
    )

    connection.execute(
        "PRAGMA foreign_keys = OFF"
    )


# ============================================================
# TAG: SQLITE SCHEMA
# ============================================================

def create_database(
    db_path: Path
) -> sqlite3.Connection:

    db_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    connection = sqlite3.connect(
        str(db_path)
    )

    configure_database(
        connection
    )

    cursor = connection.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS records (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            record_index INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,

            global_identifiers TEXT,
            address TEXT,
            name TEXT,
            property_type TEXT,

            area_m2 REAL,
            building_area_m2 REAL,
            building_floors REAL,
            apartment_floor REAL,
            rooms REAL,

            latitude REAL,
            longitude REAL
        );

        CREATE TABLE IF NOT EXISTS blocking_keys (
            key TEXT NOT NULL,
            row_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_records_source_dataset
            ON records(source, dataset_id);

        CREATE INDEX IF NOT EXISTS idx_records_fingerprint
            ON records(fingerprint);

        CREATE INDEX IF NOT EXISTS idx_blocking_keys_key
            ON blocking_keys(key);

        CREATE INDEX IF NOT EXISTS idx_blocking_keys_row
            ON blocking_keys(row_id);
        """
    )

    connection.commit()

    return connection


# ============================================================
# TAG: BLOCKING KEYS
# ============================================================

def make_blocking_keys(
    features: Dict[str, Any]
) -> List[str]:

    keys = set()

    for identifier in features[
        "global_identifiers"
    ]:

        keys.add(
            "gid:" + identifier
        )

    if features["address"]:

        keys.add(
            "address:"
            + features["address"]
        )

    if features["name"]:

        keys.add(
            "name:"
            + features["name"]
        )

    coordinates = features[
        "coordinates"
    ]

    if coordinates:

        lat, lon = coordinates

        grid_lat = round(
            lat,
            3
        )

        grid_lon = round(
            lon,
            3
        )

        keys.add(
            f"geo:{grid_lat}:{grid_lon}"
        )

    return sorted(
        keys
    )


# ============================================================
# TAG: INSERT RECORD
# ============================================================

def insert_record(
    connection: sqlite3.Connection,
    source: str,
    dataset_id: str,
    record_index: int,
    record: Dict[str, Any],
) -> int:

    features = extract_features(
        record
    )

    fingerprint = record_fingerprint(
        record
    )

    coordinates = features[
        "coordinates"
    ]

    latitude = None
    longitude = None

    if coordinates:

        latitude = coordinates[0]
        longitude = coordinates[1]

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO records (
            source,
            dataset_id,
            record_index,
            fingerprint,
            global_identifiers,
            address,
            name,
            property_type,
            area_m2,
            building_area_m2,
            building_floors,
            apartment_floor,
            rooms,
            latitude,
            longitude
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            dataset_id,
            record_index,
            fingerprint,
            json.dumps(
                features[
                    "global_identifiers"
                ],
                ensure_ascii=False
            ),
            features["address"],
            features["name"],
            features["property_type"],
            features["area_m2"],
            features["building_area_m2"],
            features["building_floors"],
            features["apartment_floor"],
            features["rooms"],
            latitude,
            longitude,
        )
    )

    row_id = cursor.lastrowid

    blocking_rows = [
        (
            key,
            row_id
        )
        for key in make_blocking_keys(
            features
        )
    ]

    if blocking_rows:

        cursor.executemany(
            """
            INSERT INTO blocking_keys (
                key,
                row_id
            )
            VALUES (?, ?)
            """,
            blocking_rows
        )

    return row_id


# ============================================================
# TAG: DATABASE -> FEATURES
# ============================================================

def db_row_to_features(
    row: sqlite3.Row
) -> Dict[str, Any]:

    coordinates = None

    if (
        row["latitude"] is not None
        and
        row["longitude"] is not None
    ):

        coordinates = (
            row["latitude"],
            row["longitude"],
        )

    return {
        "global_identifiers":
            json.loads(
                row["global_identifiers"]
                or "[]"
            ),

        "address":
            row["address"]
            or "",

        "name":
            row["name"]
            or "",

        "property_type":
            row["property_type"]
            or "",

        "area_m2":
            row["area_m2"],

        "building_area_m2":
            row["building_area_m2"],

        "building_floors":
            row["building_floors"],

        "apartment_floor":
            row["apartment_floor"],

        "rooms":
            row["rooms"],

        "coordinates":
            coordinates,
    }


# ============================================================
# TAG: BUILD INDEX
# ============================================================

def build_index(
    snapshot_dir: Path,
    db_path: Path
) -> int:

    if db_path.exists():

        db_path.unlink()

    connection = create_database(
        db_path
    )

    connection.row_factory = (
        sqlite3.Row
    )

    files = find_dataset_files(
        snapshot_dir
    )

    if not files:

        connection.close()

        print(
            "[ERROR] No dataset files found."
        )

        return 0

    total_records = 0

    for file_path in files:

        source, dataset_id = (
            identify_dataset(
                snapshot_dir,
                file_path
            )
        )

        print()
        print(
            f"[INFO] Indexing: "
            f"{file_path}"
        )

        print(
            f"[INFO] Source: {source}"
        )

        print(
            f"[INFO] Dataset: {dataset_id}"
        )

        try:

            for (
                record_index,
                record
            ) in enumerate(
                iter_json_records(
                    file_path
                )
            ):

                insert_record(
                    connection,
                    source,
                    dataset_id,
                    record_index,
                    record,
                )

                total_records += 1

                if (
                    total_records
                    % INDEX_COMMIT_BATCH
                    == 0
                ):

                    connection.commit()

                    print(
                        f"[INFO] Indexed: "
                        f"{total_records:,}"
                    )

        except Exception as error:

            print(
                "[ERROR] Failed dataset:"
            )

            print(
                file_path
            )

            print(
                error
            )

    connection.commit()

    # Restore normal journal behavior
    # after the build phase.
    connection.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    )

    connection.close()

    print()
    print(
        f"[OK] Indexed records: "
        f"{total_records:,}"
    )

    return total_records


# ============================================================
# TAG: CANDIDATE GENERATION
# ============================================================

def get_candidate_ids(
    connection: sqlite3.Connection,
    features: Dict[str, Any],
    current_row_id: Optional[int] = None,
) -> List[int]:

    keys = make_blocking_keys(
        features
    )

    if not keys:
        return []

    placeholders = ",".join(
        "?"
        for _ in keys
    )

    if current_row_id is None:

        query = f"""
            SELECT DISTINCT row_id
            FROM blocking_keys
            WHERE key IN ({placeholders})
            LIMIT ?
        """

        parameters = (
            *keys,
            MAX_CANDIDATES,
        )

    else:

        query = f"""
            SELECT DISTINCT row_id
            FROM blocking_keys
            WHERE key IN ({placeholders})
              AND row_id > ?
            ORDER BY row_id
            LIMIT ?
        """

        parameters = (
            *keys,
            current_row_id,
            MAX_CANDIDATES,
        )

    cursor = connection.execute(
        query,
        parameters
    )

    return [
        row[0]
        for row in cursor.fetchall()
    ]


# ============================================================
# TAG: MATCH DATABASE
# ============================================================

def match_database(
    db_path: Path,
    output_path: Path,
) -> Dict[str, int]:

    connection = sqlite3.connect(
        str(db_path)
    )

    connection.row_factory = (
        sqlite3.Row
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    counts = {
        "MATCH": 0,
        "POSSIBLE": 0,
        "RELATED": 0,
        "UNMATCHED": 0,
    }

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as output:

        # --------------------------------------------------------
        # TAG: PROGRESS REPORTING
        #
        # Тази фаза е най-скъпата в целия пайплайн (наблюдение
        # от 28.09.2026: 55 мин CPU и 10 GB RAM за 0 резултат).
        # Без изход тя изглежда като забит процес и човек няма
        # как да прецени дали да чака или да убие.
        # --------------------------------------------------------
        total_rows = connection.execute(
            "SELECT COUNT(*) FROM records"
        ).fetchone()[0]

        started_at = time.perf_counter()

        print(
            f"[MATCHER] Comparing {total_rows:,} records..."
        )

        processed = 0

        cursor = connection.execute(
            """
            SELECT *
            FROM records
            ORDER BY row_id
            """
        )

        for row in cursor:

            processed += 1

            if processed % 5000 == 0 or processed == total_rows:

                elapsed = (
                    time.perf_counter() - started_at
                )

                rate = (
                    processed / elapsed
                    if elapsed > 0
                    else 0
                )

                remaining = (
                    (total_rows - processed) / rate
                    if rate > 0
                    else 0
                )

                print(
                    f"[MATCHER] {processed:,}/{total_rows:,} "
                    f"({processed * 100 // max(1, total_rows)}%) "
                    f"elapsed {elapsed:.0f}s "
                    f"eta {remaining:.0f}s "
                    f"pairs={counts['MATCH'] + counts['POSSIBLE'] + counts['RELATED']:,}"
                )

            features_a = (
                db_row_to_features(
                    row
                )
            )

            candidate_ids = (
                get_candidate_ids(
                    connection,
                    features_a,
                    current_row_id=row[
                        "row_id"
                    ],
                )
            )

            if not candidate_ids:
                continue

            placeholders = ",".join(
                "?"
                for _ in candidate_ids
            )

            candidate_rows = connection.execute(
                f"""
                SELECT *
                FROM records
                WHERE row_id IN (
                    {placeholders}
                )
                """,
                candidate_ids
            ).fetchall()

            for candidate_row in (
                candidate_rows
            ):

                features_b = (
                    db_row_to_features(
                        candidate_row
                    )
                )

                result = compare_features(
                    features_a,
                    features_b
                )

                status = result[
                    "status"
                ]

                counts[status] += 1

                if status == "UNMATCHED":
                    continue

                output_record = {

                    "source_a":
                        row["source"],

                    "dataset_a":
                        row["dataset_id"],

                    "record_index_a":
                        row["record_index"],

                    "source_b":
                        candidate_row["source"],

                    "dataset_b":
                        candidate_row[
                            "dataset_id"
                        ],

                    "record_index_b":
                        candidate_row[
                            "record_index"
                        ],

                    "status":
                        status,

                    "score":
                        result["score"],

                    "signals":
                        result["signals"],

                    "conflicts":
                        result["conflicts"],
                }

                output.write(
                    json.dumps(
                        output_record,
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    connection.close()

    return counts


# ============================================================
# TAG: TEST HELPERS
# ============================================================

def test_assert(
    condition: bool,
    message: str
) -> None:

    if not condition:

        raise AssertionError(
            message
        )


# ============================================================
# TAG: AUTOMATED TESTS
# ============================================================

def run_tests() -> None:

    print(
        "================================"
    )

    print(
        " MATCHER AUTOMATED TEST V2.4"
    )

    print(
        "================================"
    )

    print()

    passed = 0
    failed = 0

    # --------------------------------------------------------
    # TEST 1
    # Field key normalization
    # --------------------------------------------------------

    try:

        values = [
            "cadastral_id",
            "cadastral-id",
            "cadastral id",
            "Cadastral ID",
        ]

        normalized = {
            normalize_field_key(value)
            for value in values
        }

        test_assert(
            normalized == {
                "cadastralid"
            },
            f"Unexpected keys: {normalized}"
        )

        print(
            "[TEST 1] Field key normalization -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 1] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 2
    # Address normalization
    # --------------------------------------------------------

    try:

        a = normalize_address(
            "бул. България 10"
        )

        b = normalize_address(
            "бул България №10"
        )

        test_assert(
            a == b,
            f"Address mismatch: "
            f"'{a}' != '{b}'"
        )

        print(
            "[TEST 2] Address normalization -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 2] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 3
    # Global ID extraction
    # --------------------------------------------------------

    try:

        record = {
            "id": 123,
            "cadastral_id":
                "68134.100.55.1",
        }

        identifiers = (
            extract_global_identifiers(
                record
            )
        )

        test_assert(
            identifiers == [
                "68134.100.55.1"
            ],
            f"Unexpected IDs: "
            f"{identifiers}"
        )

        print(
            "[TEST 3] Global ID extraction -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 3] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 4
    # Source ID exclusion
    # --------------------------------------------------------

    try:

        record = {
            "id": "ABC123",
            "record_id": "XYZ",
        }

        identifiers = (
            extract_global_identifiers(
                record
            )
        )

        test_assert(
            identifiers == [],
            f"Source IDs incorrectly detected: "
            f"{identifiers}"
        )

        print(
            "[TEST 4] Source ID exclusion -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 4] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 5
    # area1970ha
    # --------------------------------------------------------

    try:

        result = extract_area_m2(
            {
                "area1970ha": 2.5
            }
        )

        test_assert(
            result == 25000.0,
            f"Expected 25000, got {result}"
        )

        print(
            "[TEST 5] area1970ha -> "
            "25000 m² -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 5] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 6
    # area + area_unit
    # --------------------------------------------------------

    try:

        result = extract_area_m2(
            {
                "area": 25000,
                "area_unit": "m2",
            }
        )

        test_assert(
            result == 25000.0,
            f"Expected 25000, got {result}"
        )

        print(
            "[TEST 6] area + area_unit -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 6] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 7
    # dka
    # --------------------------------------------------------

    try:

        result = area_to_m2(
            1250,
            "dka"
        )

        test_assert(
            result == 1250000.0,
            f"Expected 1250000, got {result}"
        )

        print(
            "[TEST 7] 1250 dka -> "
            "1250000 m² -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 7] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 8
    # ar
    # --------------------------------------------------------

    try:

        result = area_to_m2(
            600,
            "ar"
        )

        test_assert(
            result == 60000.0,
            f"Expected 60000, got {result}"
        )

        print(
            "[TEST 8] 600 ar -> "
            "60000 m² -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 8] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 9
    # Bare area
    # --------------------------------------------------------

    try:

        result = extract_area_m2(
            {
                "area": 1250
            }
        )

        test_assert(
            result is None,
            f"Bare area incorrectly accepted: "
            f"{result}"
        )

        print(
            "[TEST 9] Bare area -> None -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 9] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 10
    # Same Global ID
    # --------------------------------------------------------

    try:

        a = {
            "cadastral_id":
                "68134.100.55.1",
            "name":
                "Building A",
        }

        b = {
            "cadastral-id":
                "68134.100.55.1",
            "name":
                "Different name",
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] == "MATCH",
            f"Expected MATCH, got {result}"
        )

        test_assert(
            result["score"] == 100.0,
            f"Expected 100, "
            f"got {result['score']}"
        )

        print(
            "[TEST 10] Same Global ID -> "
            "MATCH 100 -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 10] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 11
    # Different Global IDs
    # --------------------------------------------------------

    try:

        a = {
            "cadastral_id":
                "68134.100.55.1"
        }

        b = {
            "cadastral_id":
                "68134.100.55.2"
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] == "UNMATCHED",
            f"Expected UNMATCHED, "
            f"got {result}"
        )

        print(
            "[TEST 11] Different Global IDs -> "
            "UNMATCHED -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 11] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 12
    # Same property, different representation
    # --------------------------------------------------------

    try:

        a = {
            "address":
                "бул. България 10",
            "area":
                250,
            "area_unit":
                "m2",
            "property_type":
                "apartment",
            "building_floors":
                8,
            "apartment_floor":
                4,
            "latitude":
                42.700000,
            "longitude":
                23.300000,
            "name":
                "Апартамент 4",
        }

        b = {
            "address":
                "бул България №10",
            "area_m2":
                250,
            "property_type":
                "apartment",
            "building_floors":
                8,
            "apartment_floor":
                4,
            "lat":
                42.700000,
            "lon":
                23.300000,
            "name":
                "Апартамент 4",
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        print(
            f"[TEST 12] Expected MATCH, "
            f"Actual {result['status']}, "
            f"Score {result['score']}"
        )

        print(
            f"          Signals: "
            f"{result['signals']}"
        )

        test_assert(
            result["status"] == "MATCH",
            f"Expected MATCH, "
            f"got {result}"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 12] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 13
    # Same address, different apartment
    # --------------------------------------------------------

    try:

        a = {
            "address":
                "бул България 10",
            "apartment_floor":
                4,
            "name":
                "Апартамент 4",
        }

        b = {
            "address":
                "бул България 10",
            "apartment_floor":
                5,
            "name":
                "Апартамент 5",
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] == "UNMATCHED",
            f"Expected UNMATCHED, "
            f"got {result}"
        )

        print(
            "[TEST 13] Same address, "
            "different apartment -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 13] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 14
    # Missing information
    # --------------------------------------------------------

    try:

        a = {
            "address":
                "бул България 10",
            "area":
                250,
            "area_unit":
                "m2",
        }

        b = {
            "address":
                "бул България 10",
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] == "MATCH",
            f"Expected MATCH, "
            f"got {result}"
        )

        print(
            "[TEST 14] Missing information -> "
            "MATCH -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 14] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 15
    # Partial name
    # --------------------------------------------------------

    try:

        a = {
            "name":
                "Жилищна сграда България"
        }

        b = {
            "name":
                "Жилищна сграда България София"
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] in {
                "POSSIBLE",
                "MATCH",
            },
            f"Unexpected result: {result}"
        )

        print(
            f"[TEST 15] Partial name -> "
            f"{result['status']} -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 15] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 16
    # Area field-unit detection
    # --------------------------------------------------------

    try:

        cases = [
            (
                {"area1970ha": 2.5},
                25000.0,
            ),
            (
                {"area_dka": 1250},
                1250000.0,
            ),
            (
                {"area_ar": 600},
                60000.0,
            ),
        ]

        for record, expected in cases:

            result = extract_area_m2(
                record
            )

            test_assert(
                result == expected,
                f"{record}: "
                f"expected {expected}, "
                f"got {result}"
            )

        print(
            "[TEST 16] Area field-unit "
            "detection -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 16] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 17
    # Source IDs do not conflict
    # --------------------------------------------------------

    try:

        a = {
            "id": 100,
            "address":
                "бул България 10",
        }

        b = {
            "id": 999,
            "address":
                "бул България 10",
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            result["status"] == "MATCH",
            f"Expected MATCH, "
            f"got {result}"
        )

        print(
            "[TEST 17] Different source IDs -> "
            "not conflict -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 17] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 18
    # Coordinate match
    # --------------------------------------------------------

    try:

        a = {
            "latitude":
                42.700000,
            "longitude":
                23.300000,
        }

        b = {
            "lat":
                42.700000,
            "lon":
                23.300000,
        }

        result = compare_features(
            extract_features(a),
            extract_features(b)
        )

        test_assert(
            "coordinates_exact"
            in result["signals"],
            f"Missing coordinate signal: "
            f"{result}"
        )

        print(
            "[TEST 18] Exact coordinates -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 18] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 19
    # SQLite schema
    # --------------------------------------------------------

    try:

        test_db = (
            INDEX_DIR
            / "_test"
            / "matcher_v23_test.db"
        )

        test_db.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        if test_db.exists():
            test_db.unlink()

        connection = create_database(
            test_db
        )

        cursor = connection.cursor()

        cursor.execute(
            "PRAGMA table_info(records)"
        )

        columns = {
            row[1]
            for row in cursor.fetchall()
        }

        required = {
            "row_id",
            "source",
            "dataset_id",
            "record_index",
            "fingerprint",
            "global_identifiers",
            "address",
            "name",
            "property_type",
            "area_m2",
            "building_area_m2",
            "building_floors",
            "apartment_floor",
            "rooms",
            "latitude",
            "longitude",
        }

        test_assert(
            required.issubset(
                columns
            ),
            f"Missing columns: "
            f"{required - columns}"
        )

        connection.close()

        print(
            "[TEST 19] SQLite schema -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 19] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 20
    # Global ID variants
    # --------------------------------------------------------

    try:

        variants = [
            "cadastral_id",
            "cadastral-id",
            "cadastral id",
            "CADASTRAL_ID",
            "cadastralid",
        ]

        records = [
            {
                variant:
                    "68134.100.55.1"
            }
            for variant in variants
        ]

        extracted = [
            extract_global_identifiers(
                record
            )
            for record in records
        ]

        test_assert(
            all(
                value == [
                    "68134.100.55.1"
                ]
                for value in extracted
            ),
            f"Unexpected extraction: "
            f"{extracted}"
        )

        print(
            "[TEST 20] Global ID variants -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 20] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 21
    # X/Y are not treated as GPS
    # --------------------------------------------------------

    try:

        record = {
            "x": 472000,
            "y": 4640000,
        }

        result = extract_coordinates(
            record
        )

        test_assert(
            result is None,
            f"X/Y incorrectly detected as GPS: "
            f"{result}"
        )

        print(
            "[TEST 21] X/Y not treated as GPS -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 21] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 22
    # Invalid geographic coordinates rejected
    # --------------------------------------------------------

    try:

        record = {
            "latitude": 472000,
            "longitude": 4640000,
        }

        result = extract_coordinates(
            record
        )

        test_assert(
            result is None,
            f"Invalid coordinates accepted: "
            f"{result}"
        )

        print(
            "[TEST 22] Invalid coordinates -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 22] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 23
    # Dataset metadata is excluded
    # --------------------------------------------------------

    try:

        test_dir = (
            BASE_DIR
            / "storage"
            / "indexes"
            / "_test"
            / "dataset_discovery"
        )

        test_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        metadata = (
            test_dir
            / "metadata.json"
        )

        dataset = (
            test_dir
            / "normalized.json"
        )

        metadata.write_text(
            "{}",
            encoding="utf-8"
        )

        dataset.write_text(
            "[]",
            encoding="utf-8"
        )

        found = find_dataset_files(
            test_dir
        )

        test_assert(
            metadata not in found,
            "metadata.json was incorrectly detected"
        )

        test_assert(
            dataset in found,
            "normalized.json was not detected"
        )

        print(
            "[TEST 23] Dataset file discovery -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 23] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 24
    # Source / dataset identification
    # --------------------------------------------------------

    try:

        test_dir = (
            BASE_DIR
            / "storage"
            / "indexes"
            / "_test"
            / "source_test"
        )

    # ----------------------------------------------------
    # Test real production structure:
    #
    # snapshot/
    #   sofiaplan/
    #       dataset_626.json
    # ----------------------------------------------------

        dataset_file = (
            test_dir
            / "sofiaplan"
            / "dataset_626.json"
        )

        dataset_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        dataset_file.write_text(
            "[]",
            encoding="utf-8"
        )

        source, dataset_id = (
            identify_dataset(
                test_dir,
                dataset_file
            )
        )

        test_assert(
            source == "sofiaplan",
            f"Expected source sofiaplan, "
            f"got {source}"
        )

        test_assert(
            dataset_id == "626",
            f"Expected dataset 626, "
            f"got {dataset_id}"
        )

    # ----------------------------------------------------
    # Additional test:
    #
    # snapshot/
    #   sofiaplan/
    #       dataset_135.json
    # ----------------------------------------------------

        dataset_file_2 = (
            test_dir
            / "sofiaplan"
            / "dataset_135.json"
        )

        dataset_file_2.write_text(
            "[]",
            encoding="utf-8"
        )

        source_2, dataset_id_2 = (
            identify_dataset(
                test_dir,
                dataset_file_2
            )
        )

        test_assert(
            source_2 == "sofiaplan",
            f"Expected source sofiaplan, "
            f"got {source_2}"
        )

        test_assert(
            dataset_id_2 == "135",
            f"Expected dataset 135, "
            f"got {dataset_id_2}"
        )

    # ----------------------------------------------------
    # Test alternative supported structure:
    #
    # snapshot/
    #   sofiaplan/
    #       626/
    #           normalized.json
    # ----------------------------------------------------

        alternative_file = (
            test_dir
            / "sofiaplan"
            / "626"
            / "normalized.json"
        )

        alternative_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        alternative_file.write_text(
            "[]",
            encoding="utf-8"
        )

        source_3, dataset_id_3 = (
            identify_dataset(
                test_dir,
                alternative_file
            )
        )

        test_assert(
            source_3 == "sofiaplan",
            f"Expected source sofiaplan, "
            f"got {source_3}"
        )

        test_assert(
            dataset_id_3 == "626",
            f"Expected dataset 626, "
            f"got {dataset_id_3}"
        )

        print(
            "[TEST 24] Source/dataset identification -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 24] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # TEST 25
    # Candidate generation uses row_id ordering
    # --------------------------------------------------------

    try:

        test_db = (
            INDEX_DIR
            / "_test"
            / "candidate_v23_test.db"
        )

        test_db.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        if test_db.exists():
            test_db.unlink()

        connection = create_database(
            test_db
        )

        connection.row_factory = (
            sqlite3.Row
        )

        features = {
            "global_identifiers": [],
            "address": "булевард българия 10",
            "name": "",
            "property_type": "",
            "area_m2": None,
            "building_area_m2": None,
            "building_floors": None,
            "apartment_floor": None,
            "rooms": None,
            "coordinates": None,
        }

        row_ids = []

        for index in range(3):

            row_id = insert_record(
                connection,
                "sofiaplan",
                "626",
                index,
                {
                    "address":
                        "булевард българия 10"
                }
            )

            row_ids.append(
                row_id
            )

        connection.commit()

        candidates = get_candidate_ids(
            connection,
            features,
            current_row_id=row_ids[0]
        )

        test_assert(
            all(
                candidate > row_ids[0]
                for candidate in candidates
            ),
            f"Invalid candidate ordering: "
            f"{candidates}"
        )

        connection.close()

        print(
            "[TEST 25] Candidate row ordering -> PASS"
        )

        passed += 1

    except Exception as error:

        print(
            f"[TEST 25] FAIL: {error}"
        )

        failed += 1

    # --------------------------------------------------------
    # CLEAN TEST ARTIFACTS
    # --------------------------------------------------------

    try:

        cleanup_paths = [
            BASE_DIR
            / "storage"
            / "indexes"
            / "_test"
            / "dataset_discovery",

            BASE_DIR
            / "storage"
            / "indexes"
            / "_test"
            / "source_test",
        ]

        for path in cleanup_paths:

            if path.exists():

                for child in sorted(
                    path.rglob("*"),
                    reverse=True
                ):

                    if child.is_file():
                        child.unlink()

                for child in sorted(
                    path.rglob("*"),
                    reverse=True
                ):

                    if child.is_dir():
                        child.rmdir()

                path.rmdir()

    except Exception:
        pass

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    print()

    print(
        "================================"
    )

    print(
        f"RESULT {passed} passed, "
        f"{failed} failed"
    )

    print(
        "================================"
    )

    if failed:

        raise SystemExit(
            1
        )


# ============================================================
# TAG: MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Property Data Matcher V2.4"
        )
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Run automated tests",
    )

    parser.add_argument(
        "--build-index",
        action="store_true",
        help="Build SQLite matcher index",
    )

    parser.add_argument(
        "--match",
        action="store_true",
        help="Run matching against SQLite index",
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite database path",
    )

    parser.add_argument(
        "--input",
        type=Path,
        help="Input snapshot directory",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSONL file",
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # TEST
    # --------------------------------------------------------

    if args.test:

        run_tests()

        return

    # --------------------------------------------------------
    # BUILD INDEX
    # --------------------------------------------------------

    if args.build_index:

        input_dir = args.input

        if input_dir is None:

            input_dir = (
                find_latest_snapshot(
                    DEDUPLICATED_DIR
                )
            )

        if input_dir is None:

            print(
                "[ERROR] No deduplicated "
                "snapshot found."
            )

            raise SystemExit(
                1
            )

        print(
            "================================"
        )

        print(
            " MATCHER INDEX BUILDER V2.3"
        )

        print(
            "================================"
        )

        print()

        print(
            f"Input: {input_dir}"
        )

        build_index(
            input_dir,
            args.db
        )

        print()

        print(
            f"[DONE] Database: "
            f"{args.db}"
        )

        return

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    if args.match:

        output = args.output

        if output is None:

            now = datetime.now()

            timestamp = now.strftime(
                "%d-%m-%Y_%H"
            )

            output = (
                MATCHED_DIR
                / timestamp
                / "match_results.jsonl"
            )

        if not args.db.exists():

            print(
                "[ERROR] Matcher database "
                "does not exist."
            )

            print(
                "Run --build-index first."
            )

            raise SystemExit(
                1
            )

        print(
            "================================"
        )

        print(
            " MATCHER V2.3"
        )

        print(
            "================================"
        )

        print()

        print(
            f"Database: {args.db}"
        )

        print(
            f"Output: {output}"
        )

        counts = match_database(
            args.db,
            output
        )

        print()

        print(
            "================================"
        )

        print(
            "MATCH RESULTS"
        )

        for status, count in (
            counts.items()
        ):

            print(
                f"{status:<10} "
                f"{count:,}"
            )

        print(
            "================================"
        )

        return

    parser.print_help()


if __name__ == "__main__":
    main()