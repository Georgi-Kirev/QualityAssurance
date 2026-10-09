# TAG: NORMALIZER V4.4
# Универсален streaming Normalizer + Validator.
#
# Основни функции:
# 1. Открива всички RAW snapshots.
# 2. Групира snapshots по SOURCE + DATASET ID.
# 3. Избира само най-новия RAW snapshot за всеки dataset.
# 4. Един dataset = една worker задача.
# 5. Проверява за duplicate jobs преди стартиране.
# 6. Разпознава формата по съдържанието.
# 7. Поддържа legacy raw_data.bin, ако съдържа JSON/GeoJSON/CSV.
# 8. Чете RAW streaming чрез ijson.
# 9. Записва normalized data streaming.
# 10. Валидира крайния normalized файл streaming.
# 11. Проверява очаквания брой записи.
# 12. Retry само при подходящи грешки.
# 13. Не оставя невалиден финален JSON.
# 14. Запазва всички source attributes.
# 15. Запазва geometry само веднъж.
# 16. Нормализира разпознати площи.
# 17. Използва паралелна обработка.
# 18. Изолира грешка в един dataset от останалите.


import codecs
import csv
import json
import math
import os
import re
import sys
import time

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import ijson

from tools.parallel_runner import run_paced
from tools import currency as fx
from tools.resources import (
    calculate_resources,
    lower_process_priority,
    plan_workers,
)


# ============================================================
# TAG: PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

RAW_DIR = BASE_DIR / "storage_raw"
NORMALIZED_DIR = BASE_DIR / "storage" / "normalized"


# ============================================================
# TAG: CONFIGURATION
# ============================================================

SOURCE_DEFAULT = "sofiaplan"

# ---------------------------------------------------------
# TAG: ИЗТОЧНИЦИ, КОИТО НЕ СА ИМОТИ
#
# ПРОВЕРЕНО ЕМПИРИЧНО НА 28.09.2026:
#
#   NSI HPI_2.1-en.xlsx -> 1241 записа
#
#   Всичките са АГРЕГАТНИ ИНДЕКСИ, не имотни обяви:
#     Total HPI 2026-Q1 = 109.39, 2026-Q2 = 114.27
#     Yugozapaden 2026-Q2 Total HPI = 113.04
#
#   Ако влязат в пайплайна, дават 1241 "имота", всеки без:
#     - адрес
#     - геометрия / локация
#     - площ
#     - цена
#   Измерено: 1241/1241 празни адресни подписи, 1241
#   отделни property_id-та. Броят на имотите в продукта
#   става неверен, а таблиците се пълнят с безсмислици.
#
#   Причината да е ИЗРИЧНО ИЗКЛЮЧЕН тук, а не "да не ги
#   свалим": файлът вече е на диск и е валиден. Изключването
#   е на ниво ИЗТОЧНИК, защото агрегатните данни нямат
#   адрес по определение и никога не стават имот.
#
#   Къде им принадлежи: reference слой за дашборда
#   (контекст на пазара), отделен от property трека.
#   Виж collector/nsi.py и aggregate_dataset_HPI_2.1.json.
# ---------------------------------------------------------
NON_PROPERTY_SOURCES = {
    "nsi",
    "egov",
}

# Initial attempt + maximum 2 retries.
MAX_ATTEMPTS = 3

# Максимално количество данни, оглеждано при разпознаване на формата.
FORMAT_SNIFF_BYTES = 1024 * 1024

# Само тези файлове се смятат за реални RAW файлове.
#
# "raw_data.txt" е ДОБАВЕН защото SofiaPlan обслужва някои
# dataset-и (включително 624 - ЕДИНСТВЕНИЯ с цени) като
# text/plain, а fetcher-ът ги записва с .txt разширение.
# Без този запис 144 dataset-а се сваляха и после МЪЛЧАХ -
# тихо, без грешка, и целият ценови слой изчезваше от
# продукта.
RAW_FILENAMES = {
    "raw_data.geojson",
    "raw_data.json",
    "raw_data.csv",
    "raw_data.txt",
    "raw_data.bin",
}

# Префикс на всички RAW файлове. Ако добавим нов формат
# по-нататък, е достатъчно да го пишем след "_" и да не
# трябва да редактираме списъка.
RAW_FILENAME_PREFIX = "raw_data"


# ============================================================
# TAG: JSON SERIALIZATION
# ============================================================

def json_default(value):
    """
    Convert values which standard json cannot serialize.
    """

    if isinstance(value, Decimal):

        number = float(value)

        if math.isfinite(number):
            return number

        return None

    if isinstance(value, Path):
        return str(value)

    raise TypeError(
        f"Object of type {type(value).__name__} "
        f"is not JSON serializable"
    )


# ============================================================
# TAG: TIME
# ============================================================

def current_timestamp():
    """
    Bulgaria local timestamp.

    Format:
        DD-MM-YYYY_HH
    """

    now = datetime.now(
        ZoneInfo("Europe/Sofia")
    )

    return now.strftime(
        "%d-%m-%Y_%H"
    )


# ============================================================
# TAG: FORMAT DETECTION
# ============================================================

def decode_text_sample(sample):
    """
    Decode a text sample which may be CUT IN THE MIDDLE of a
    multi-byte character.

    Returns the decoded text, or None when the sample is not
    text at all.

    ЗАЩО СЕ ИМПЛЕМЕНТИРА ТАК, А НЕ ПРОСТО sample.decode(...)

    Пробата за разпознаване е с LIMIT (1 MiB). Ако в този
    момент байтът FORMAT_SNIFF_BYTES - 1 е първата половина на
    двубайтов UTF-8 символ, строгият decode хвърля грешка
    "unexpected end of data" - НЕ защото файлът е двоичен, а
    защото пробata е отрязана.

    Измерено на 28.09.2026 (поправката е от този ден):
    точно това се случи с 4 dataset-а (181, 274, 479, 568),
    всичките валиден GeoJSON на общо 118 422 features.
    Строгият decode падаше, падаше на UTF-16 (който "успява"
    върху боклук), файлът излиза "txt" и молчаливо отпадаше от
    пайплайна. Същото като липсващия "raw_data.txt" от
    27.09.2026 - тихо, без грешка, без следа в логовете.

    Инкременталният decoder с final=False БУФЕРИРА недовършения
    последен символ вместо да хвърля грешка. Разликата е точно
    "допустимо отрязване" срещу "истински двоичен файл".

    ЗАЩО UTF-16 Е САМО ПО BOM

    Старият код падаше на UTF-16 при ВСЕКА неуспешна проба
    utf-8. Но UTF-16 decode-ът на произволни байтове почти винаги
    "успява" - връща безсмислен текст, който после не минава
    нито JSON, нито CSV проверката, и файлът получава етикет
    "txt". Тоест fallback-ът, който не може да се провали, е
    по-опасен от липсата му: той превръща една ясна грешка в
    една неясна.

    Проверено на всичките 370 RAW файла: НИТО ЕДИН не е
    UTF-16. 338 се четат чисто с UTF-8, 32 са истински
    двоични. Затова UTF-16 се опитва само при реален BOM -
    иначе "bin" е честният отговор.

    ПРОВЕРЕНО ЕМПИРИЧНО, НЕ Е ГАДАЕНЕ. Ако някой ден източник
    започне да сервира UTF-16, ще го открие BOM-ът.
    """

    for encoding in ("utf-8-sig", "utf-16"):
        decoder = codecs.getincrementaldecoder(
            encoding
        )()

        try:
            text = decoder.decode(
                sample,
                False,
            )

        except UnicodeDecodeError:
            continue

        if encoding == "utf-16" and not (
            sample.startswith(b"\xff\xfe")
            or sample.startswith(b"\xfe\xff")
            or sample.startswith(b"\xff\xfe\x00\x00")
        ):
            # Няма BOM -> не е UTF-16. Успехът на decode-а
            # тук е случайност, не доказателство.
            continue

        return text

    return None


def classify_binary_container(sample):
    """
    Name the binary container of a RAW file.

    Purpose: when a dataset is REJECTED, the reason has to be
    specific. "Unsupported RAW file format" for a 146 MB
    GeoTIFF and for a 27 KB spreadsheet are the same sentence
    and teach nothing. Naming the container makes the rejection
    auditable and stops the next person from assuming the data
    was merely "not found yet".

    Verified against the real files on 28.09.2026 by their
    leading bytes:

        %PDF-          -> 4 x application/pdf
        7z\\xbc\\xaf'\\x1c     -> 11 x application/x-7z-compressed
        II*\\x00 / MM\\x00   ->  7 x image/tiff (BigTIFF, ~100 MB)
        \\xd0\\xcf\\x11\\xe0      ->  2 x application/vnd.ms-excel
        PK\\x03\\x04        -> OOXML / QGIS bundle (see note)

    The PK case needs the file itself, because "PK" covers
    xlsx, docx and plain zip. The distinction only matters for
    the report, never for correctness: none of them can become
    a property record, which is why all of them are rejected.
    """

    if sample.startswith(b"%PDF-"):
        return "pdf"

    if sample.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"

    if sample.startswith(b"II*\x00") or sample.startswith(b"MM\x00*"):
        return "tiff"

    if sample.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2"

    if sample.startswith(b"PK\x03\x04"):
        return "zip"

    if sample.startswith(b"\x1f\x8b"):
        return "gzip"

    if sample.startswith(b"Rar!"):
        return "rar"

    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if sample.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return "bin"


# ---------------------------------------------------------
# TAG: ЗАЩО ОТХВЪРЛЯМЕ - КЛАСИФИКАЦИЯ
#
# Номенклатурата е проверена срещу metadata.json на
# всичките 369 dataset-а на 28.09.2026. Тя описва какво
# ФАКТИЧЕСКИ съдържат файловете, а не какво предполагаме.
#
#   189  application/json
#   148  text/plain;charset=UTF-8
#    11  application/x-7z-compressed
#     7  image/tiff
#     5  xlsx
#     4  application/pdf
#     2  vnd.ms-excel
#     2  application/zip
#     1  docx
#   ----
#   369
#
# Изводът е важен и е в полза на проекта, не срещу него:
# 337 dataset-а са текст и влизат нормално. Останалите 32 са
# ДОКУМЕНТИ И РАСТРИ - не имотни обяви. Нито един PDF, нито
# един GeoTIFF, нито един .xlsx не може да даде имот.
#
# Затова отхвърлянето им е ПРАВИЛНО. Грешно беше само че то
# беше безшумно и некласифицирано - изглеждаше като загуба на
# данни, а всъщност е разпознаване на нещо друго.
#
# Същото като NON_PROPERTY_SOURCES: превръщането на 7 GeoTIFF-а
# в "имоти" би дало фантомни имоти точно като случая с НСИ.
# ---------------------------------------------------------

REJECTION_REASONS = {
    "pdf": (
        "PDF document",
        "not a tabular/vector dataset",
    ),
    "7z": (
        "7-Zip archive",
        "packed container, not a property dataset",
    ),
    "tiff": (
        "GeoTIFF raster",
        "pixel grid, no address/price/geometry per feature",
    ),
    "ole2": (
        "legacy MS Office binary (.xls)",
        "spreadsheet, requires an OLE2 parser we do not have",
    ),
    "zip": (
        "ZIP container (xlsx / docx / QGIS bundle)",
        "packed container, not a property dataset",
    ),
    "gzip": (
        "gzip stream",
        "compressed, not a property dataset",
    ),
    "bin": (
        "unrecognized binary",
        "leading bytes match no known container",
    ),
    "txt": (
        "plain text, not JSON/CSV",
        "no tabular structure recognized in the head",
    ),
}


def describe_rejection(
    path: Path,
    detected_format: str,
):
    """
    Explain, specifically, why a RAW file cannot enter the
    property pipeline.

    Returns a dict with a stable machine key plus a
    human-readable label and reason, so the final report can
    group rejections instead of listing 32 identical and
    useless error strings.
    """

    # --------------------------------------------------------
    # "txt" се ЗАПАЗВА. Той е различна диагноза от "bin":
    # файлът е ЧЕТИМ, просто не е таблица. Понижаването му
    # до "bin" би повторило точно мълчанието, което тази
    # поправка премахва.
    # --------------------------------------------------------

    if detected_format == "txt":

        label, reason = REJECTION_REASONS["txt"]

        return {
            "container": "txt",
            "detected_format": "txt",
            "label": label,
            "reason": reason,
            "property_candidate": False,
        }

    # За "zip"/"gzip" името на контейнера вече е в
    # detected_format. Без този клон `container` оставаше
    # неинициализиран и describe_rejection() падаше с
    # UnboundLocalError точно за 8-те ZIP dataset-а -
    # тоест отхвърлянето им ставаше некласифицирано и
    # изглеждаше като загуба на данни.
    if detected_format in {"zip", "gzip"}:

        container = detected_format

    else:

        with path.open("rb") as file:
            container = classify_binary_container(
                file.read(64)
            )

    label, reason = REJECTION_REASONS.get(
        container,
        REJECTION_REASONS["bin"],
    )

    return {
        "container": container,
        "detected_format": detected_format,
        "label": label,
        "reason": reason,
        "property_candidate": False,
    }



def sniff_file(path: Path):
    """
    Detect the actual content format.

    File extension is intentionally ignored.

    This allows legacy files such as:

        raw_data.bin

    to be recognized as JSON/GeoJSON if their content
    actually contains JSON data.
    """

    with path.open("rb") as file:

        sample = file.read(
            FORMAT_SNIFF_BYTES
        )

    if not sample:

        raise ValueError(
            f"RAW file is empty: {path}"
        )

    # --------------------------------------------------------
    # TAG: BINARY SIGNATURES
    #
    # Checked BEFORE any text decoding. A 7z archive opens with
    # bytes that are not valid UTF-8, but relying on a decode
    # failure to detect it is what caused the bug described in
    # decode_text_sample() - a decode can fail for reasons that
    # have nothing to do with the file being binary.
    # --------------------------------------------------------

    container = classify_binary_container(
        sample
    )

    if container != "bin":

        if container == "gzip":

            return "gzip"

        if container == "zip":

            return "zip"

        return "bin"

    # --------------------------------------------------------
    # TAG: TEXT DECODING
    # --------------------------------------------------------

    text = decode_text_sample(
        sample
    )

    if text is None:

        return "bin"

    text = text.lstrip()

    if not text:

        raise ValueError(
            f"RAW file contains no readable content: {path}"
        )

    # --------------------------------------------------------
    # TAG: JSON / GEOJSON
    # --------------------------------------------------------

    if text.startswith("{"):

        if re.search(
            r'"type"\s*:\s*"FeatureCollection"',
            text,
            re.IGNORECASE,
        ):
            return "geojson"

        return "json"

    if text.startswith("["):
        return "json"

    # --------------------------------------------------------
    # TAG: CSV
    # --------------------------------------------------------

    first_line = text.splitlines()[0]

    try:

        dialect = csv.Sniffer().sniff(
            first_line
        )

        if dialect.delimiter in {
            ",",
            ";",
            "\t",
            "|",
        }:

            return "csv"

    except Exception:
        pass

    return "txt"


# ============================================================
# TAG: JSON STRUCTURE DETECTION
# ============================================================

def detect_json_structure(path: Path):
    """
    Detect JSON structure without loading the whole file.

    Supported structures:

        GeoJSON FeatureCollection
        root array
        records[]
        data[]
        items[]
        results[]
        single object
    """

    with path.open("rb") as file:

        sample = file.read(
            FORMAT_SNIFF_BYTES
        )

    text = sample.decode(
        "utf-8-sig",
        errors="replace",
    ).lstrip()

    # --------------------------------------------------------
    # TAG: GEOJSON
    # --------------------------------------------------------

    if re.search(
        r'"type"\s*:\s*"FeatureCollection"',
        text,
        re.IGNORECASE,
    ):

        return "features.item"

    # --------------------------------------------------------
    # TAG: ROOT ARRAY
    # --------------------------------------------------------

    if text.startswith("["):
        return "item"

    # --------------------------------------------------------
    # TAG: COMMON JSON WRAPPERS
    # --------------------------------------------------------

    for key in (
        "records",
        "data",
        "items",
        "results",
    ):

        pattern = (
            rf'"{key}"\s*:\s*\['
        )

        if re.search(
            pattern,
            text,
            re.IGNORECASE,
        ):

            return f"{key}.item"

    # --------------------------------------------------------
    # TAG: SINGLE OBJECT
    # --------------------------------------------------------

    return None


# ============================================================
# TAG: JSON RECORD ITERATOR
# ============================================================

def iter_json_records(path: Path):
    """
    Streaming JSON iterator.

    The entire dataset is never loaded into RAM.
    """

    structure = detect_json_structure(
        path
    )

    with path.open("rb") as file:

        if structure:

            for record in ijson.items(
                file,
                structure,
                use_float=True,
            ):

                yield record

            return

        # ----------------------------------------------------
        # TAG: SINGLE OBJECT
        # ----------------------------------------------------

        for record in ijson.items(
            file,
            "",
            use_float=True,
        ):

            yield record


# ============================================================
# TAG: CSV RECORD ITERATOR
# ============================================================

def iter_csv_records(path: Path):

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:
            yield dict(row)


# ============================================================
# TAG: RAW RECORD ITERATOR
# ============================================================

def iter_raw_records(path: Path):
    """
    Universal RAW record iterator.
    """

    detected_format = sniff_file(
        path
    )

    if detected_format in {
        "json",
        "geojson",
    }:

        yield from iter_json_records(
            path
        )

        return

    if detected_format == "csv":

        yield from iter_csv_records(
            path
        )

        return

    raise ValueError(
        f"Unsupported RAW file format: "
        f"{path} "
        f"(detected: {detected_format})"
    )


# ============================================================
# TAG: AREA NORMALIZATION
# ============================================================

def normalize_area(
    field_name,
    value,
):
    """
    Detect common area units.

    Canonical units:

        m2
        dka
        ar
        ha

    IMPORTANT:
    A bare numeric value without recognizable unit
    is NOT automatically interpreted as area.
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:

        numeric_value = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if not math.isfinite(
        numeric_value
    ):

        return None

    # Площ 0 или отрицателна никога не е валидна. Ако не
    # се филтрира тук, detect_area() може да избере 0 вместо
    # реалното площно поле на записа.
    if numeric_value <= 0:

        return None

    name = str(
        field_name
    ).lower()

    normalized_name = (
        name
        .replace("²", "2")
        .replace("-", "_")
        .replace(" ", "_")
    )

    # --------------------------------------------------------
    # TAG: HECTARE
    #
    # "ha" се търси като ЦЯЛ ТОКЕН, не като substring -
    # иначе "shape"/"share"/"phases"/"characteristics"
    # съдържат "ha" и биха умножени с 10000.
    # --------------------------------------------------------

    if (
        re.search(
            r"(^|[_])ha($|[_])",
            normalized_name,
        )
        or "hectare" in normalized_name
        or "hectares" in normalized_name
    ):

        square_meters = (
            numeric_value * 10000
        )

        return {
            "value": numeric_value,
            "unit": "ha",
            "square_meters": square_meters,
            "decares": square_meters / 1000,
            "ares": square_meters / 100,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: DECARE
    # --------------------------------------------------------

    if (
        "dka" in normalized_name
        or "decare" in normalized_name
        or "decares" in normalized_name
    ):

        square_meters = (
            numeric_value * 1000
        )

        return {
            "value": numeric_value,
            "unit": "dka",
            "square_meters": square_meters,
            "decares": numeric_value,
            "ares": numeric_value * 10,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: DA
    # --------------------------------------------------------

    if re.search(
        r"(^|[_])da($|[_])",
        normalized_name,
    ):

        square_meters = (
            numeric_value * 1000
        )

        return {
            "value": numeric_value,
            "unit": "dka",
            "square_meters": square_meters,
            "decares": numeric_value,
            "ares": numeric_value * 10,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: SQUARE METERS (Bulgarian kv_m / kvm — преди ARE)
    # --------------------------------------------------------

    if (
        "kv_m" in normalized_name
        or "kvm" in normalized_name
        or "kvadr" in normalized_name
        or re.search(r"plosht.*m2", normalized_name)
    ):

        square_meters = numeric_value

        return {
            "value": numeric_value,
            "unit": "m2",
            "square_meters": square_meters,
            "decares": square_meters / 1000,
            "ares": square_meters / 100,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: ARE
    # --------------------------------------------------------

    if (
        re.search(
            r"(^|[_])ar($|[_])",
            normalized_name,
        )
        or re.search(r"(^|[_])ares($|[_])", normalized_name)
    ):

        square_meters = (
            numeric_value * 100
        )

        return {
            "value": numeric_value,
            "unit": "ar",
            "square_meters": square_meters,
            "decares": square_meters / 1000,
            "ares": numeric_value,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: SQUARE KILOMETERS (km2)
    #
    # ДОБАВЕНО на 28.09.2026. ТРЯБВА ДА Е ПРЕДИ "m2",
    # защото името "area_km2" съдържа "m2" и без този клон
    # клона за m2 го е хващал с множител 1.
    #
    # Измерено: dataset 604, 564 записа. "2.4" от area_km2
    # се записваше като 2.4 m2 вместо 2 400 000 m2.
    # Стойността 2.4 е правдоподобна и в метри, затова
    # грешката е невидима без проверка на мащаба.
    # --------------------------------------------------------

    if (
        "km2" in normalized_name
        or "km^2" in normalized_name
        or "sqkm" in normalized_name
        or "squarekilometer" in normalized_name
        or re.search(
            r"kvadrat.*kilomet",
            normalized_name,
        )
    ):

        square_meters = (
            numeric_value * 1000000
        )

        return {
            "value": numeric_value,
            "unit": "km2",
            "square_meters": square_meters,
            "decares": square_meters / 1000,
            "ares": square_meters / 100,
            "source_field": field_name,
        }

    # --------------------------------------------------------
    # TAG: SQUARE METERS
    # --------------------------------------------------------

    if (
        "m2" in normalized_name
        or "sqm" in normalized_name
        or "squaremeter" in normalized_name
        or "square_meter" in normalized_name
    ):

        square_meters = numeric_value

        return {
            "value": numeric_value,
            "unit": "m2",
            "square_meters": square_meters,
            "decares": square_meters / 1000,
            "ares": square_meters / 100,
            "source_field": field_name,
        }

    return None


# ============================================================
# TAG: PRICE NORMALIZATION
# ============================================================

def _field_name_matches(name: str, candidates) -> bool:
    key = str(name).lower().replace("-", "_").replace(" ", "_")
    for cand in candidates:
        c = cand.lower()
        if key == c or key.endswith(f"_{c}") or c in key:
            return True
    return False


def detect_price(attributes, source=None):
    """
    Открива цена и я превежда в EUR.

    source: името на източника ("sofiaplan", ...). Определя в коя
           валута да се чете поле без изричен валутен суфикс.
           Без него се приема EUR, което за български източници
           означава ~2x грешка. Виж tools/currency.py.
    """
    if not isinstance(attributes, dict):
        return None

    price_fields = [
        "price",
        "price_eur",
        "price_bgn",
        "cena",
        "цена",
        "cena_eur",
        "cena_bgn",
    ]

    native_currency = fx.source_currency(source)

    for field_name, raw_value in attributes.items():
        if not _field_name_matches(field_name, price_fields):
            continue

        try:
            numeric_value = float(
                str(raw_value)
                .replace(",", ".")
                .replace(" ", "")
                .replace("€", "")
                .replace("лв.", "")
                .replace("EUR", "")
                .replace("BGN", "")
            )
        except (TypeError, ValueError):
            continue

        if not math.isfinite(numeric_value) or numeric_value <= 0:
            continue

        fname = str(field_name).lower()

        # 1) Изричен валутен суфикс в името на полето -> най-надежден.
        if "bgn" in fname or "лв" in fname:
            detected_currency = "BGN"
        elif "eur" in fname or "€" in fname:
            detected_currency = "EUR"
        else:
            # 2) Без суфикс -> чужда за България дума ("cena", "price").
            #    Не бива да се приема за EUR на сляпо; използва се
            #    естествената валута на източника.
            detected_currency = native_currency

        value_eur = fx.to_eur(numeric_value, detected_currency)

        if value_eur is None:
            continue

        block = {
            "value_eur": round(value_eur, 2),
            "currency": "EUR",
            "raw_value": numeric_value,
            "source_field": field_name,
        }

        if detected_currency != "EUR":
            block["source_currency"] = detected_currency
            block["source_value"] = numeric_value
            block.update(
                fx.build_price_fields(numeric_value, detected_currency)
            )

        return block

    return None


# ============================================================
# TAG: GEOMETRY EXTRACTION
# ============================================================

def extract_geometry(record):

    if not isinstance(
        record,
        dict,
    ):

        return None

    geometry = record.get(
        "geometry"
    )

    if isinstance(
        geometry,
        dict,
    ):

        return geometry

    return None


# ============================================================
# TAG: ATTRIBUTE EXTRACTION
# ============================================================

def extract_attributes(record):
    """
    Preserve all original source properties.
    """

    if not isinstance(
        record,
        dict,
    ):

        return {
            "value": record
        }

    properties = record.get(
        "properties"
    )

    if isinstance(
        properties,
        dict,
    ):

        return dict(
            properties
        )

    return dict(
        record
    )


# ============================================================
# TAG: AREA DETECTION
# ============================================================

def detect_area(attributes):

    if not isinstance(
        attributes,
        dict,
    ):

        return None

    preferred = []

    for field_name in attributes:

        if "area" in str(
            field_name
        ).lower():

            preferred.append(
                field_name
            )

    ordered_fields = (
        preferred
        + [
            field
            for field in attributes
            if field not in preferred
        ]
    )

    for field_name in ordered_fields:

        result = normalize_area(
            field_name,
            attributes[field_name],
        )

        if result:
            return result

    return None


# ============================================================
# TAG: RECORD NORMALIZATION
# ============================================================

def normalize_record(
    record,
    source,
    dataset_id,
):
    """
    Convert one RAW record to common structure.
    """

    attributes = extract_attributes(
        record
    )

    normalized = {
        "source": source,
        "dataset_id": dataset_id,
        "attributes": attributes,
    }

    # --------------------------------------------------------
    # TAG: ID
    # --------------------------------------------------------

    if isinstance(
        record,
        dict,
    ):

        record_id = record.get(
            "id"
        )

        if record_id is not None:

            normalized["id"] = record_id

    # --------------------------------------------------------
    # TAG: GEOMETRY
    # --------------------------------------------------------

    geometry = extract_geometry(
        record
    )

    if geometry is not None:

        normalized["location"] = {
            "geometry": geometry
        }

    # --------------------------------------------------------
    # TAG: AREA
    # --------------------------------------------------------

    area = detect_area(
        attributes
    )

    if area is not None:

        normalized["area"] = area

    # --------------------------------------------------------
    # TAG: PRICE
    # --------------------------------------------------------

    price = detect_price(
        attributes,
        source=source
    )

    if price is not None:

        normalized["price"] = price

    return normalized


# ============================================================
# TAG: RAW SNAPSHOT DISCOVERY
# ============================================================

def discover_raw_snapshots():
    """
    Discover every real RAW file.

    Important:
    metadata.json and other JSON files are ignored.
    """

    snapshots = []
    skipped_sources = []

    if not RAW_DIR.exists():
        return snapshots

    for source_dir in RAW_DIR.iterdir():

        if not source_dir.is_dir():
            continue

        source = source_dir.name

        # TAG: ИЗКЛЮЧВАНЕ НА АГРЕГАТНИ ИЗТОЧНИЦИ
        #
        # Проверката е ТУК, в откриването, а не по-късно -
        # защото тук вече е ясно кой източник е. Ако се
        # направи във всеки отделен етап, един и същ
        # пропуск може да се прояви на три места и да даде
        # три различни (и всеки пъти тихи) резултата.
        if source in NON_PROPERTY_SOURCES:
            skipped_sources.append(source)
            continue

        for date_dir in source_dir.iterdir():

            if not date_dir.is_dir():
                continue

            datasets_dir = (
                date_dir / "datasets"
            )

            if not datasets_dir.exists():
                continue

            for dataset_dir in datasets_dir.iterdir():

                if not dataset_dir.is_dir():
                    continue

                try:

                    dataset_id = int(
                        dataset_dir.name
                    )

                except ValueError:

                    continue

                for path in dataset_dir.rglob("*"):

                    if not path.is_file():
                        continue

                    # Позволени са както изброените формати, така
                    # и всеки бъдещ raw_data.<нещо>. Съдържанието
                    # се разпознава от sniff_file(), не от
                    # разширението - затова едно ново разширение
                    # не би внесло мълчалива загуба на данни.
                    name = path.name

                    if (
                        name not in RAW_FILENAMES
                        and not name.startswith(
                            RAW_FILENAME_PREFIX
                        )
                    ):
                        continue

                    snapshots.append(
                        {
                            "source": source,
                            "dataset_id": dataset_id,
                            "raw_file": path,
                            "date_dir": date_dir,
                        }
                    )

    return snapshots


# ============================================================
# TAG: UNIQUE DATASET JOBS
# ============================================================

def build_unique_dataset_jobs(
    snapshots
):
    """
    Group all RAW snapshots by:

        source + dataset_id

    and select exactly one latest RAW file.

    This is the critical protection against duplicate workers.
    """

    groups = {}

    for snapshot in snapshots:

        key = (
            snapshot["source"],
            snapshot["dataset_id"],
        )

        groups.setdefault(
            key,
            [],
        ).append(
            snapshot
        )

    jobs = []

    for key, items in groups.items():

        latest = max(
            items,
            key=lambda item: (
                item["raw_file"].stat().st_mtime
            ),
        )

        jobs.append(
            {
                "source": latest["source"],
                "dataset_id": latest["dataset_id"],
                "raw_file": latest["raw_file"],
                "snapshot_count": len(items),
                "snapshot_date": latest["date_dir"].name,
            }
        )

    jobs.sort(
        key=lambda item: (
            item["source"],
            item["dataset_id"],
        )
    )

    return jobs


# ============================================================
# TAG: DUPLICATE JOB VALIDATION
# ============================================================

def validate_unique_jobs(jobs):
    """
    Verify that each source + dataset ID exists exactly once.
    """

    seen = set()
    duplicates = []

    for job in jobs:

        key = (
            job["source"],
            job["dataset_id"],
        )

        if key in seen:

            duplicates.append(
                key
            )

        seen.add(
            key
        )

    return duplicates


# ============================================================
# TAG: EXPECTED RECORD COUNT
# ============================================================

def find_expected_record_count(
    source,
    dataset_id,
):
    """
    Find row_count from the newest catalog snapshot.
    """

    source_dir = (
        RAW_DIR / source
    )

    catalogs = []

    if not source_dir.exists():
        return None

    for date_dir in source_dir.iterdir():

        if not date_dir.is_dir():
            continue

        catalog = (
            date_dir /
            f"{source}_datasets.json"
        )

        if catalog.exists():

            catalogs.append(
                catalog
            )

    if not catalogs:
        return None

    catalog = max(
        catalogs,
        key=lambda p: p.stat().st_mtime,
    )

    try:

        data = json.loads(
            catalog.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return None

    if not isinstance(
        data,
        list,
    ):

        return None

    for item in data:

        if not isinstance(
            item,
            dict,
        ):

            continue

        if item.get(
            "id"
        ) != dataset_id:

            continue

        row_count = item.get(
            "row_count"
        )

        if row_count is None:
            return None

        try:

            return int(
                row_count
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

    return None


# ============================================================
# TAG: OUTPUT VALIDATION
# ============================================================

def validate_output(
    output_file,
    expected_count,
):
    """
    Validate normalized JSON by streaming.

    Checks:

        1. File exists.
        2. File is not empty.
        3. JSON can be parsed.
        4. Every record is an object.
        5. Record count matches expected count.
    """

    if not output_file.exists():

        return {
            "valid": False,
            "count": 0,
            "error": (
                "Output file does not exist."
            ),
        }

    if output_file.stat().st_size == 0:

        return {
            "valid": False,
            "count": 0,
            "error": (
                "Output file is empty."
            ),
        }

    count = 0

    try:

        with output_file.open(
            "rb"
        ) as file:

            for record in ijson.items(
                file,
                "item",
                use_float=True,
            ):

                if not isinstance(
                    record,
                    dict,
                ):

                    return {
                        "valid": False,
                        "count": count,
                        "error": (
                            "Output contains "
                            "a non-object record."
                        ),
                    }

                count += 1

    except Exception as error:

        return {
            "valid": False,
            "count": count,
            "error": (
                "Invalid normalized JSON: "
                f"{type(error).__name__}: "
                f"{error}"
            ),
        }

    if expected_count is not None:

        if count != expected_count:

            return {
                "valid": False,
                "count": count,
                "error": (
                    "Expected record count mismatch: "
                    f"expected={expected_count}, "
                    f"actual={count}"
                ),
            }

    return {
        "valid": True,
        "count": count,
        "error": None,
    }


# ============================================================
# TAG: NORMALIZE ONE DATASET
# ============================================================

def normalize_dataset_once(
    job,
    output_dir,
):
    """
    Normalize exactly one unique dataset job.
    """

    source = job[
        "source"
    ]

    dataset_id = job[
        "dataset_id"
    ]

    raw_file = Path(
        job["raw_file"]
    )

    expected_count = (
        find_expected_record_count(
            source,
            dataset_id,
        )
    )

    output_file = (
        output_dir /
        source /
        f"dataset_{dataset_id}.json"
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = Path(
        str(output_file)
        + ".tmp"
    )

    # --------------------------------------------------------
    # TAG: CLEAN TEMP FILE
    # --------------------------------------------------------

    if temp_file.exists():

        try:

            temp_file.unlink()

        except PermissionError:

            raise PermissionError(
                "Temporary output file is locked: "
                f"{temp_file}"
            )

    if output_file.exists():

        try:

            output_file.unlink()

        except PermissionError:

            raise PermissionError(
                "Final output file is locked: "
                f"{output_file}"
            )

    start = time.time()

    # --------------------------------------------------------
    # TAG: FORMAT DETECTION
    # --------------------------------------------------------

    detected_format = sniff_file(
        raw_file
    )

    if detected_format not in {
        "json",
        "geojson",
        "csv",
    }:

        raise ValueError(
            f"Unsupported RAW file format: "
            f"{raw_file} "
            f"(detected: {detected_format})"
        )

    # --------------------------------------------------------
    # TAG: STREAMING NORMALIZATION
    # --------------------------------------------------------

    count = 0

    with temp_file.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output:

        output.write("[")

        first = True

        for record in iter_raw_records(
            raw_file
        ):

            normalized = normalize_record(
                record,
                source,
                dataset_id,
            )

            if not first:
                output.write(",")

            json.dump(
                normalized,
                output,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
                default=json_default,
            )

            first = False

            count += 1

        output.write("]")

    # --------------------------------------------------------
    # TAG: VALIDATE TEMP OUTPUT
    # --------------------------------------------------------

    validation = validate_output(
        temp_file,
        expected_count,
    )

    if not validation[
        "valid"
    ]:

        if temp_file.exists():

            try:
                temp_file.unlink()
            except Exception:
                pass

        raise ValueError(
            validation["error"]
        )

    # --------------------------------------------------------
    # TAG: ATOMIC FINALIZATION
    # --------------------------------------------------------

    try:

        os.replace(
            temp_file,
            output_file,
        )

    except PermissionError as error:

        raise PermissionError(
            "Could not finalize normalized file. "
            "The file may be locked by another process: "
            f"{output_file}"
        ) from error

    elapsed = (
        time.time() - start
    )

    return {
        "source": source,
        "dataset_id": dataset_id,
        "raw_file": str(raw_file),
        "snapshot_date": job[
            "snapshot_date"
        ],
        "snapshot_count": job[
            "snapshot_count"
        ],
        "count": count,
        "expected": expected_count,
        "elapsed": elapsed,
        "file": str(output_file),
        "format": detected_format,
    }


# ============================================================
# TAG: WORKER
# ============================================================

def process_dataset(
    job,
    output_dir,
):
    """
    Process one UNIQUE dataset.

    Important:
    One source + dataset ID can only reach this function
    once because duplicate jobs are removed before the
    ProcessPoolExecutor starts.
    """

    source = job[
        "source"
    ]

    dataset_id = job[
        "dataset_id"
    ]

    raw_file = Path(
        job["raw_file"]
    )

    last_error = None

    # --------------------------------------------------------
    # TAG: EARLY FORMAT CHECK
    # --------------------------------------------------------

    try:

        detected_format = sniff_file(
            raw_file
        )

        if detected_format not in {
            "json",
            "geojson",
            "csv",
        }:

            detail = describe_rejection(
                raw_file,
                detected_format,
            )

            return {
                "status": "failed",
                "source": source,
                "dataset_id": dataset_id,
                "error": (
                    "ValueError: "
                    "Unsupported RAW file format: "
                    f"{raw_file} "
                    f"(detected: {detected_format})"
                ),
                "rejection": detail,
                "attempts": 1,
                "permanent": True,
            }

    except Exception as error:

        return {
            "status": "failed",
            "source": source,
            "dataset_id": dataset_id,
            "error": (
                f"{type(error).__name__}: "
                f"{error}"
            ),
            "attempts": 1,
            "permanent": True,
        }

    # --------------------------------------------------------
    # TAG: NORMALIZATION ATTEMPTS
    # --------------------------------------------------------

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        try:

            result = normalize_dataset_once(
                job,
                output_dir,
            )

            result[
                "attempt"
            ] = attempt

            return {
                "status": "success",
                "result": result,
            }

        except PermissionError as error:

            # File locking is normally permanent for this run.
            # Retrying from another worker would not make sense
            # because every dataset already has a unique worker.
            last_error = (
                f"PermissionError: "
                f"{error}"
            )

            return {
                "status": "failed",
                "source": source,
                "dataset_id": dataset_id,
                "error": last_error,
                "attempts": attempt,
                "permanent": True,
            }

        except ValueError as error:

            last_error = (
                f"ValueError: "
                f"{error}"
            )

            error_text = str(
                error
            )

            if (
                "Unsupported RAW file format"
                in error_text
                or "RAW file is empty"
                in error_text
                or "no readable content"
                in error_text
            ):

                return {
                    "status": "failed",
                    "source": source,
                    "dataset_id": dataset_id,
                    "error": last_error,
                    "attempts": attempt,
                    "permanent": True,
                }

            if attempt < MAX_ATTEMPTS:
                continue

        except Exception as error:

            last_error = (
                f"{type(error).__name__}: "
                f"{error}"
            )

            if attempt < MAX_ATTEMPTS:
                continue

    return {
        "status": "failed",
        "source": source,
        "dataset_id": dataset_id,
        "error": last_error,
        "attempts": MAX_ATTEMPTS,
        "permanent": False,
    }


# ============================================================
# TAG: CANCEL CLEANUP
# ============================================================

def cleanup_partial_run(
    output_dir: Path,
    abandoned_jobs: list,
) -> int:
    """
    Изчиства артефактите от прекъснат normalizer run.

    Три нива:
      1. .tmp файловете на недовършените задачи
         (тези, които са били в движение при отказа)
      2. .tmp файловете на ВСИЧКИ в този output_dir
         (за всеки случай, ако някой процес е бил убит
          без да успее да ги изтрие)
      3. Празните директории, останали след почистването

    ВНИМАние: output_dir НЕ се изтрива целият. Нормализираните
    файлове на УСПЕШНО завършилите dataset-и остават валидни.
    metadata.json НЕ се записва при отказ, затова този run
    няма да бъде ползван от следващите стъпки.
    """

    removed = 0

    if not output_dir.exists():

        return 0

    # ---- 1. .tmp на недовършените задачи ----

    for job in abandoned_jobs or []:

        try:

            candidate = (
                output_dir /
                job["source"] /
                f"dataset_{job['dataset_id']}.json.tmp"
            )

            if candidate.exists():

                candidate.unlink()

                removed += 1

        except Exception:

            pass

    # ---- 2. всеки .tmp в този run ----

    try:

        for temp_file in output_dir.rglob(
            "*.json.tmp"
        ):

            try:

                temp_file.unlink()

                removed += 1

            except Exception:

                pass

    except Exception:

        pass

    # ---- 3. празни директории ----

    try:

        for directory in sorted(
            output_dir.rglob("*"),
            key=lambda p: len(p.parts),
            reverse=True,
        ):

            try:

                if (
                    directory.is_dir()
                    and not any(directory.iterdir())
                ):

                    directory.rmdir()

            except Exception:

                pass

    except Exception:

        pass

    return removed


# ============================================================
# TAG: MAIN
# ============================================================

def main(cancel_event=None):

    print(
        "================================"
    )

    print(
        " NORMALIZER V4.4"
    )

    print(
        "================================"
    )

    print()

    # --------------------------------------------------------
    # TAG: RESOURCE INFORMATION
    # --------------------------------------------------------

    resources = calculate_resources()

    print(
        f"[RESOURCE] Physical CPU cores: "
        f"{resources['cpu_count']}"
    )

    print(
        f"[RESOURCE] Available RAM: "
        f"{resources['available_ram_gb']:.2f} GB"
    )

    print(
        f"[RESOURCE] CPU target: "
        f"{int(resources['cpu_target'] * 100)}%"
    )

    print(
        f"[RESOURCE] RAM target: "
        f"{int(resources['ram_target'] * 100)}%"
    )

    print(
        f"[RESOURCE] CPU workers limit: "
        f"{resources['cpu_workers']}"
    )

    print(
        f"[RESOURCE] RAM workers limit: "
        f"{resources['ram_workers']}"
    )

    print(
        f"[RESOURCE] Reserved cores: "
        f"{resources['reserved_cores']}"
    )

    print(
        f"[RESOURCE] Hard ceiling: "
        f"{resources['ceiling']}"
    )

    print(
        f"[RESOURCE] Workers at start: "
        f"{resources['workers']}"
    )

    print(
        f"[RESOURCE] Workers are scaled "
        f"DYNAMICALLY during the run."
    )

    print()

    # --------------------------------------------------------
    # TAG: RAW SNAPSHOT DISCOVERY
    # --------------------------------------------------------

    snapshots = discover_raw_snapshots()

    print(
        f"[INFO] RAW snapshots found: "
        f"{len(snapshots)}"
    )

    # TAG: ВИДИМО ОБЯСНЕНИЕ ЗА ИЗКЛЮЧЕНИТЕ ИЗТОЧНИЦИ
    #
    # Изключването трябва да се ВИЖДА. Иначе изглежда,
    # че наличните данни просто не са било намерени -
    # същият извод, който вече ни измами веднъж при
    # липсващия "raw_data.txt".
    if NON_PROPERTY_SOURCES:
        print(
            f"[NOTE] Skipped non-property sources "
            f"(aggregate/reference data, not listings): "
            f"{', '.join(sorted(NON_PROPERTY_SOURCES))}"
        )
        print(
            f"[NOTE] They are kept under storage_raw and are "
            f"NOT property records. Excluding them keeps the "
            f"property count truthful."
        )

    # --------------------------------------------------------
    # TAG: UNIQUE JOB BUILD
    # --------------------------------------------------------

    jobs = build_unique_dataset_jobs(
        snapshots
    )

    print(
        f"[INFO] Unique dataset groups: "
        f"{len(jobs)}"
    )

    duplicate_snapshot_count = (
        len(snapshots)
        - len(jobs)
    )

    print(
        f"[INFO] Additional snapshots grouped: "
        f"{duplicate_snapshot_count}"
    )

    # --------------------------------------------------------
    # TAG: DUPLICATE JOB SAFETY CHECK
    # --------------------------------------------------------

    duplicates = validate_unique_jobs(
        jobs
    )

    if duplicates:

        print()

        print(
            "[ERROR] Duplicate dataset jobs detected."
        )

        for source, dataset_id in duplicates:

            print(
                f"  - "
                f"{source}/{dataset_id}"
            )

        print()

        print(
            "[ERROR] Normalizer stopped before "
            "starting workers."
        )

        return 1

    if not jobs:

        print()

        print(
            "[WARNING] No datasets found."
        )

        return 1

    # --------------------------------------------------------
    # TAG: OUTPUT DIRECTORY
    # --------------------------------------------------------

    run_timestamp = current_timestamp()

    output_dir = (
        NORMALIZED_DIR
        / run_timestamp
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # TAG: INVALIDATE STALE RUN MARKER
    # --------------------------------------------------------
    #
    # run_timestamp е по час, затова няколко пускания в
    # един и същ час споделят папка. Ако предишен run е
    # оставил metadata.json, а новият бъде прекъснат, той
    # щеше да изглежда завършен за dedup / consolidator.
    #
    # Инвариант: metadata.json съществува <=> run-ът е
    # завършил успешно. Затова го изтриваме В НАЧАЛОТО.

    metadata_file = (
        output_dir / "metadata.json"
    )

    if metadata_file.exists():

        try:

            metadata_file.unlink()

            print()

            print(
                f"[INFO] Removed stale metadata.json "
                f"from previous run in this folder."
            )

        except Exception as error:

            print()

            print(
                f"[WARN] Could not remove stale "
                f"metadata.json: {error}"
            )

    print()

    print(
        "[INFO] Output directory:"
    )

    print(
        output_dir
    )

    print()

    # --------------------------------------------------------
    # TAG: JOB SUMMARY
    # --------------------------------------------------------

    print(
        "[INFO] Worker job list:"
    )

    for job in jobs:

        print(
            f"  - "
            f"{job['source']}/"
            f"{job['dataset_id']} "
            f"-> "
            f"{job['snapshot_date']} "
            f"| snapshots: "
            f"{job['snapshot_count']}"
        )

    print()

    print(
        f"[INFO] Running with "
        f"{resources['workers']} workers."
    )

    print()

    # --------------------------------------------------------
    # TAG: RESULT STORAGE
    # --------------------------------------------------------

    successful = []
    failed = []

    total_records = 0

    # --------------------------------------------------------
    # TAG: PARALLEL EXECUTION (dynamic workers)
    # --------------------------------------------------------
    #
    # Пулът се създава с максималния таван, но броят на
    # едновременно изпълнявани задачи се решава ДИНАМИЧНО
    # от run_paced според моментното натоварване.

    max_workers = resources["ceiling"]

    def handle_result(
        job,
        result,
        error,
    ):

        nonlocal total_records

        if error is not None:

            result = {
                "status": "failed",
                "source": job["source"],
                "dataset_id": job["dataset_id"],
                "error": (
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
                "attempts": 1,
            }

        if result["status"] == "success":

            info = result["result"]

            successful.append(info)

            total_records += info["count"]

            expected_text = ""

            if info["expected"] is not None:

                expected_text = (
                    f"/{info['expected']:,}"
                )

            print()

            print(
                f"[OK] "
                f"{job['source']}/{job['dataset_id']} "
                f"-> "
                f"{info['count']:,}"
                f"{expected_text} records "
                f"| RAW: "
                f"{info['snapshot_date']} "
                f"| snapshots: "
                f"{info['snapshot_count']} "
                f"| attempt "
                f"{info['attempt']} "
                f"| "
                f"{info['elapsed']:.1f}s"
            )

        else:

            failed.append(result)

            print()

            print(
                f"[FAILED] "
                f"{job['source']}/{job['dataset_id']}"
            )

            print(
                f"         "
                f"{result['error']}"
            )

            print(
                f"         "
                f"Attempts: "
                f"{result['attempts']}"
            )

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=lower_process_priority,
    ) as executor:

        run = run_paced(
            jobs,
            process_dataset,
            executor,
            on_result=handle_result,
            worker_kwargs={
                "output_dir": output_dir,
            },
            cancel_event=cancel_event,
            target_workers=max_workers,
            on_critical=lambda plan: print(
                f"[RESOURCE] BLOCKED: "
                f"{'; '.join(plan['reasons'])}"
            ),
        )

    # --------------------------------------------------------
    # TAG: CANCEL CLEANUP
    # --------------------------------------------------------

    if run["cancelled"]:

        print()

        print(
            f"[CANCEL] Stopped by user. "
            f"Cleaning partial output..."
        )

        removed = cleanup_partial_run(
            output_dir,
            run["abandoned"],
        )

        print(
            f"[CANCEL] Removed {removed} partial "
            f"artifact(s) from "
            f"{output_dir}"
        )

        print(
            f"[CANCEL] No metadata.json is written "
            f"- this run is incomplete and will be "
            f"ignored by later steps."
        )

    # --------------------------------------------------------
    # TAG: RUN METADATA
    # --------------------------------------------------------
    #
    # При отказ metadata.json НЕ се записва изобщо — така
    # прекъснатият run няма да бъде приет за валиден от
    # dedup / consolidator / exporter.

    if run["cancelled"]:

        return 130

    # --------------------------------------------------------
    # TAG: ОТХВЪРЛЕНИ ПО СЪДЪРЖАНИЕ
    #
    # metadata.json е това, което следващият човек чете, когато
    # се чуди защо 32 dataset-а липсват. Без тази секция
    # отговорът е "failed: 32" - което звучи като повреда.
    # С нея отговорът е "11 7z, 7 GeoTIFF, 5 xlsx, 4 PDF,
    # 2 .xls, 1 docx, 2 QGIS bundle: не са имотни обяви".
    # --------------------------------------------------------

    rejected_by_container = {}

    for item in failed:

        detail = item.get("rejection")

        if not detail:
            continue

        key = detail["container"]

        entry = rejected_by_container.setdefault(
            key,
            {
                "label": detail["label"],
                "reason": detail["reason"],
                "count": 0,
                "dataset_ids": [],
            },
        )

        entry["count"] += 1
        entry["dataset_ids"].append(
            item["dataset_id"]
        )

    for entry in rejected_by_container.values():
        entry["dataset_ids"].sort()

    metadata = {
        "normalizer_version": "4.4",
        "run_timestamp": run_timestamp,
        "source_default": SOURCE_DEFAULT,

        "rejected_not_property_data": {
            "total": sum(
                e["count"]
                for e in rejected_by_container.values()
            ),
            "by_container": rejected_by_container,
        },

        "raw_snapshots_found": len(
            snapshots
        ),

        "unique_dataset_groups": len(
            jobs
        ),

        "additional_snapshots_grouped": (
            duplicate_snapshot_count
        ),

        "datasets_found": len(
            jobs
        ),

        "successful": len(
            successful
        ),

        "failed": len(
            failed
        ),

        "records_normalized": (
            total_records
        ),

        "physical_cpu_cores": resources[
            "cpu_count"
        ],

        "reserved_cores": resources[
            "reserved_cores"
        ],

        "workers_ceiling": resources[
            "ceiling"
        ],

        "available_ram_gb": round(
            resources[
                "available_ram_gb"
            ],
            2,
        ),

        "cpu_target": resources["cpu_target"],
        "ram_target": resources["ram_target"],

        "workers": resources[
            "workers"
        ],

        "workers_dynamic": True,

        "max_attempts": MAX_ATTEMPTS,
    }

    metadata_file.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        ),
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # TAG: FINAL REPORT
    # --------------------------------------------------------

    print()

    print(
        "================================"
    )

    print(
        " NORMALIZER V4.4 FINISHED"
    )

    print(
        "================================"
    )

    print()

    print(
        f"[RESULT] RAW snapshots found: "
        f"{len(snapshots)}"
    )

    print(
        f"[RESULT] Unique datasets: "
        f"{len(jobs)}"
    )

    print(
        f"[RESULT] Successful: "
        f"{len(successful)}"
    )

    print(
        f"[RESULT] Failed: "
        f"{len(failed)}"
    )

    print(
        f"[RESULT] Records normalized: "
        f"{total_records:,}"
    )

    if failed:

        print()

        print(
            "[WARNING] Failed datasets:"
        )

        # --------------------------------------------------------
        # TAG: ГРУПИРАНО ОБЯСНЕНИЕ
        #
        # 32-те отхвърлени dataset-а не са една и съща
        # грешка. Без разделяне изглеждат като 32 неуспеха
        # на пайплайна, което кара някой да търси проблем в
        # нормализатора. С разделяне се вижда, че това са
        # документи и растри, които по дефиниция не са
        # имотни обяви.
        # --------------------------------------------------------

        rejected = {}

        for item in failed:

            detail = item.get("rejection")

            if not detail:
                continue

            key = detail["container"]

            rejected.setdefault(
                key,
                {
                    "label": detail["label"],
                    "reason": detail["reason"],
                    "datasets": [],
                },
            )["datasets"].append(
                item["dataset_id"]
            )

        if rejected:

            print()

            print(
                "[INFO] Rejections grouped by real content:"
            )

            for key in sorted(
                rejected,
                key=lambda k: -len(rejected[k]["datasets"]),
            ):

                group = rejected[key]

                ids = ", ".join(
                    str(i) for i in sorted(group["datasets"])
                )

                print()

                print(
                    f"  {group['label']} "
                    f"({len(group['datasets'])} dataset-а)"
                )

                print(
                    f"    -> {group['reason']}"
                )

                print(
                    f"    -> ids: {ids}"
                )

        print()

        for item in failed:

            print(
                f"  - "
                f"{item['source']}/"
                f"{item['dataset_id']}"
            )

            print(
                f"    Error: "
                f"{item['error']}"
            )

    print()

    print(
        "[INFO] Metadata:"
    )

    print(
        metadata_file
    )

    return 0


# ============================================================
# TAG: PROGRAM START
# ============================================================

if __name__ == "__main__":
    sys.exit(main())