# TAG: NSI PARSER
# Превръща файловете на НСИ в unified schema на пайплайна.
#
# ИЗТОЧНИК:
#   Национален статистически институт (https://www.nsi.bg)
#   Официална държавна статистика.
#
# ЗАЩО НУЖЕН ОТДЕЛЕН ПАРСЕР:
#   НСИ НЕ ползва REST API и НЕ ползва GeoJSON. Публикува
#   собствени формати:
#     * .regnp - XML на НСИ (структурирани наблюдения)
#     * .xlsx  - Excel справки (трябва да се чете като ZIP+XML)
#     * .csv   - рядко, но се среща
#   Всички те трябва да станат записи, които същият
#   normalizer/dedup/consolidate пайплайн може да обработи.
#
# ЦЕЛ:
#   От индекс на цени (HPI) да получим запис, който агентът
#   може да ползва като МАКРОКОНТЕКСТ за пазара - не като имот,
#   а като "цената в София-град се е повишила с 4.5%".
#
# ЧЕСТНО ЗА ФОРМАТИТЕ:
#   Структурата на .regnp е документирана от НСИ и е стабилна
#   от години, но тук НЕ е твърдение, че всички варианти са
#   покрити. Затова parser-ът:
#     * връща разпознат формат или None - никога не
#       измисля стойности;
#     * пази суровия файл непроменен в storage_raw;
#     * записва diagnostics при всеки неуспех, за да е
#       видимо какво точно не е разпознато.
#
# ЛЕГАЛНО: robots.txt на НСИ разрешава автоматично четене на
#   публичното съдържание (забранен е само /admin/).

import csv
import io
import json
import re
import zipfile
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree


# TAG: DETECTED FORMATS

class NsiFormat:
    """Разпознати формати на входни файлове."""

    REGNP = "regnp"
    XLSX = "xlsx"
    CSV = "csv"
    UNKNOWN = "unknown"


def detect_format(
    filename: str,
    head: bytes,
) -> str:
    """
    Определя формата по ИМЕ и по СЪДЪРЖАНИЕТО.

    Името е водещият сигнал (НСИ спазва конвенция за
    разширенията), съдържанието е проверката.
    """
    lower = filename.lower()

    if lower.endswith(".regnp"):
        return NsiFormat.REGNP
    if lower.endswith((".xlsx", ".xlsm")):
        return NsiFormat.XLSX
    if lower.endswith(".csv"):
        return NsiFormat.CSV

    # XLSX и ZIP-ът започват с "PK\x03\x04"
    if head[:4] == b"PK\x03\x04":
        return NsiFormat.XLSX

    # REGNP е UTF-8 XML
    stripped = head.lstrip()
    if stripped[:1] == b"<":
        return NsiFormat.REGNP

    # Иначе опитваме CSV
    try:
        text = head[:2048].decode("utf-8-sig")
    except UnicodeDecodeError:
        return NsiFormat.UNKNOWN

    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.count(",") >= 2:
        return NsiFormat.CSV

    return NsiFormat.UNKNOWN


# TAG: REGNP PARSER

def parse_regnp(payload: bytes) -> Dict[str, Any]:
    """
    Чете .regnp XML на НСИ.

    Структура (съкратено):
        <REGNP>
          <META><TITLE>...</TITLE><NAME>...</NAME></META>
          <OBS>
            <VALS><OBS_VALUE>...</OBS_VALUE></VALS>
            <VAL_DSC>...</VAL_DSC>
            <OBS_DIM>...</OBS_DIM>
          </OBS>
        </REGNP>

    Реалната схема е по-богата и варира между отчетите.
    Тук се извлича ОНОВНОТО, което е сигурно: списък от
    наблюдения, всяко като dict от име->стойност.

    Връща {'format', 'title', 'series', 'records',
            'diagnostics'}.
    """
    diagnostics: List[str] = []

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        return {
            "format": NsiFormat.REGNP,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": [
                f"XML parse error: {error}"
            ],
        }

    title = None
    for tag in ("TITLE", "NAME"):
        node = root.find(f".//{tag}")
        if node is not None and node.text:
            title = node.text.strip()
            break

    if title is None:
        diagnostics.append(
            "No <TITLE>/<NAME> in META - title unknown"
        )

    # TAG: SERIES DEFINITIONS
    series: List[Dict[str, str]] = []
    for dim in root.findall(".//SER_DIM"):
        code = dim.get("DIM_CODE") or dim.get("code") or ""
        name_node = dim.find("SER_DIM_LABELS/SER_DIM_LABEL")
        name = (
            name_node.text.strip()
            if name_node is not None and name_node.text
            else code
        )
        series.append({"code": code, "name": name})

    if not series:
        diagnostics.append(
            "No <SER_DIM> definitions - column names unknown"
        )

    # TAG: OBSERVATIONS
    records: List[Dict[str, Any]] = []

    for obs in root.findall(".//OBS"):
        values: List[str] = []
        for value_node in obs.findall("VALS/OBS_VALUE"):
            values.append(
                (value_node.text or "").strip()
            )

        if not values:
            continue

        # Ако има SERIES_DEF, първата стойност е индексът ѝ
        record: Dict[str, Any] = {}
        offset = 0

        series_def = obs.find("SERIES_DEF")
        if series_def is not None and series_def.text:
            offset = 1

        for position, value in enumerate(values):
            if position < offset:
                continue
            index = position - offset
            if index < len(series):
                record[series[index]["name"]] = value
            else:
                record[f"col_{index}"] = value

        if record:
            records.append(record)

    if not records:
        diagnostics.append(
            "No <OBS> observations found - file may be an "
            "empty or metadata-only export"
        )

    return {
        "format": NsiFormat.REGNP,
        "title": title,
        "series": series,
        "records": records,
        "diagnostics": diagnostics,
    }


# TAG: XLSX PARSER
#
# XLSX е ZIP архив с XML вътре. Не добавяме openpyxl като
# зависимост - четем само sheet-а и споделената таблица със
# стандартната библиотека. Това е достатъчно за отчетите на
# НСИ, които са плоски таблици.
_XL_NS = {
    "main": "http://schemas.openxmlformats.org/"
            "spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/"
           "officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/"
           "package/2006/relationships",
}


def _column_letters(reference: str) -> int:
    """
    "B7" -> 1 (нумеруция от 0), за да се подредят клетките.
    """
    letters = re.match(r"([A-Z]+)", reference or "")
    if not letters:
        return 0
    total = 0
    for char in letters.group(1):
        total = total * 26 + (ord(char) - ord("A") + 1)
    return total - 1


def parse_xlsx(payload: bytes) -> Dict[str, Any]:
    """
    Чете първия worksheet от XLSX файл.

    Първият ред се приема за заглавия. Редове, чиято първа
    клетка е празна, се пропускат - при отчетите на НСИ те
    са между заглавията и не носят данни.
    """
    diagnostics: List[str] = []

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": [
                f"Not a valid XLSX/ZIP: {error}"
            ],
        }

    sheet_names = [
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet")
    ]

    if not sheet_names:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": ["No worksheet found in archive"],
        }

    # TAG: SHARED STRINGS
    shared: List[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        try:
            root = ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml")
            )
            for item in root.findall("main:si", _XL_NS):
                parts = [
                    node.text or ""
                    for node in item.iter(
                        "{http://schemas.openxmlformats.org/"
                        "spreadsheetml/2006/main}t"
                    )
                ]
                shared.append("".join(parts))
        except ElementTree.ParseError as error:
            diagnostics.append(
                f"sharedStrings.xml unreadable: {error}"
            )

    # TAG: FIRST SHEET
    try:
        sheet = ElementTree.fromstring(
            archive.read(sheet_names[0])
        )
    except (ElementTree.ParseError, KeyError) as error:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": [f"Sheet unreadable: {error}"],
        }

    grid: List[List[Any]] = []

    for row_node in sheet.iter(
        "{http://schemas.openxmlformats.org/"
        "spreadsheetml/2006/main}row"
    ):
        cells: Dict[int, Any] = {}

        for cell in row_node:
            if not cell.tag.endswith("}c"):
                continue

            reference = cell.get("r") or ""
            cell_type = cell.get("t") or "n"

            value_node = cell.find(
                "{http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main}v"
            )
            inline_node = cell.find(
                "{http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main}is"
            )

            if cell_type == "s" and value_node is not None:
                try:
                    text = shared[int(value_node.text or 0)]
                except (ValueError, IndexError):
                    text = ""
            elif cell_type == "inlineStr" and inline_node is not None:
                text = "".join(
                    node.text or ""
                    for node in inline_node.iter(
                        "{http://schemas.openxmlformats.org/"
                        "spreadsheetml/2006/main}t"
                    )
                )
            elif value_node is not None:
                text = value_node.text or ""
            else:
                text = ""

            cells[_column_letters(reference)] = text

        if not cells:
            continue

        width = max(cells) + 1
        grid.append([
            cells.get(index, "") for index in range(width)
        ])

    if not grid:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": ["Worksheet is empty"],
        }

    # TAG: HEADER DETECTION
    # Търси първия ред с поне 2 непразни текстови клетки.
    # При отчетите на НСИ преди таблицата често има заглавие и
    # единици, затова не вземаме просто първия ред.
    header_index = None
    for index, row in enumerate(grid[:15]):
        filled = [
            cell for cell in row
            if isinstance(cell, str) and cell.strip()
        ]
        if len(filled) >= 2:
            header_index = index
            break

    if header_index is None:
        diagnostics.append(
            "No header row found in the first 15 rows"
        )
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": diagnostics,
        }

    header = [
        (str(cell).strip() if cell else f"col_{index}")
        for index, cell in enumerate(grid[header_index])
    ]

    # Уникални заглавия - иначе празните колони стават
    # еднакви ключове и се презаписват.
    #
    # Пример защо: НСИ често има празна колона за единици
    # ("100, 150, 200"). Без уникализация двете празни
    # колони биха дали един и същ ключ и една от тях да
    # презапише другата.
    seen: Dict[str, int] = {}
    unique_header: List[str] = []
    for index, name in enumerate(header):
        if not name:
            name = f"col_{index}"
        count = seen.get(name, 0)
        if count:
            name = f"{name}_{count + 1}"
        seen[name] = count + 1
        unique_header.append(name)

    records: List[Dict[str, Any]] = []

    for row in grid[header_index + 1:]:
        if not any(
            str(cell).strip() for cell in row if cell
        ):
            continue

        record = {
            unique_header[index]: (
                str(cell).strip() if cell else ""
            )
            for index, cell in enumerate(row)
            if index < len(unique_header)
        }

        if any(record.values()):
            records.append(record)

    return {
        "format": NsiFormat.XLSX,
        "title": " | ".join(
            cell for cell in unique_header[:6] if cell
        ),
        "series": [
            {"code": name, "name": name}
            for name in unique_header
        ],
        "records": records,
        "diagnostics": diagnostics,
    }


# TAG: CSV PARSER

def parse_csv(payload: bytes) -> Dict[str, Any]:
    """
    Чете CSV. НСИ ползва ';', ',' или tab като разделител -
    определя се автоматично.
    """
    diagnostics: List[str] = []

    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = payload.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    else:
        return {
            "format": NsiFormat.CSV,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": ["Undecodable text"],
        }

    sample = text[:4096]

    delimiter = ","
    best = sample.count(",")
    for candidate in (";", "\t", "|"):
        count = sample.count(candidate)
        if count > best:
            best = count
            delimiter = candidate

    reader = csv.reader(
        io.StringIO(text),
        delimiter=delimiter,
    )

    rows = [row for row in reader]

    if not rows:
        return {
            "format": NsiFormat.CSV,
            "title": None,
            "series": [],
            "records": [],
            "diagnostics": ["Empty file"],
        }

    header = [
        (cell.strip() or f"col_{index}")
        for index, cell in enumerate(rows[0])
    ]

    records = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        records.append({
            header[index]: cell.strip()
            for index, cell in enumerate(row)
            if index < len(header)
        })

    diagnostics.append(
        f"Decoded as {used_encoding}, delimiter {delimiter!r}"
    )

    return {
        "format": NsiFormat.CSV,
        "title": " | ".join(header[:6]),
        "series": [
            {"code": name, "name": name} for name in header
        ],
        "records": records,
        "diagnostics": diagnostics,
    }


# TAG: WIDE TIME-SERIES PARSER (реалният формат на НСИ)
#
# ОТКРИТО НА ЖИВО 28.09.2026 - HPI_2.1-en.xlsx (21 817 байта):
#
#   row 1 : A = "2.1 HPI by statistical regions - 2025=100"
#   row 2 : A = "(index levels)"
#   row 3 : G,K,O,... = "(%)"          <- проценти, отделни
#                                      измервания
#   row 4 : A=Code  B=Type of purchase
#           C=Statistical zones and statistical regions
#           D=2015  H=2016  L=2017 ... AR=2025  AV=2026   <- ГОДИНИ
#   row 5 : D=" ? " E="I ?" F="II ?" G="IV"  H=" ? " ... <- ТРИМЕСЕЧИЯ
#   row 6 : A=H.1.  B=Total HPI  C=Total  D=39.66 E=40.01 ... <- ДАННИ
#   row 7 : C=Severna I Yugoiztochna Bulgaria  D=44.72 ...      <- РЕГИОН
#
# Т.е. това е ШИРОКА таблица: редовете са обекти (региони),
# колоните са ПЕРИОДИ. Плоският парсер връщаше col_0..col_51
# и напълно губеше смисъла.
#
# ИЗХОД - LONG формат (един запис на измерване):
#   {code, purchase_type, geography, period, value, unit}
#   33 региона x ~44 периода = ~1450 записа от един файл.
#
# ЗАЩО long, а не wide: агентите питат "каква е стойността за
# София-град през Q2 2026". Wide таблица изисква от тях да
# знаят къде е Q2 2026 в 51-колонна редица. Long е тривиално
# за заявка и за човек.

_ROW_LABEL_CACHE: Dict[str, int] = {}


def _cell_text(
    value: Any,
    shared: List[str],
) -> str:
    """Извлича текста на клетка, резолвирайки shared string."""
    if value is None:
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+", text) and shared:
        index = int(text)
        if 0 <= index < len(shared):
            return shared[index].strip()
    return text


def _looks_like_year(value: str) -> bool:
    """
    True за '2015', '2025', '20253', '20262,3'.

    Реалният файл използва '2025' и '20253' за последните
    две години. Първата версия използваше
    re.fullmatch(r"20\\d{2}3?") - което НЕ разпознаваше
    '20262,3' и 2026-те колони наследяваха 2025.
    """
    return bool(re.match(r"^\s*20\d{2}", value or ""))


# TAG: QUARTER LABELS - КОДИРОВКАТА Е СЧУПЕНА В САМИЯ ФАЙЛ
#
# Проверено на живо 28.09.2026 върху HPI_2.1-en.xlsx -
# sharedStrings.xml съдържа точно тези стойности:
#
#     10  repr=' ?'     <- Q1  (степният индекс е заместен с '?')
#     11  repr=' I?'    <- Q2
#     12  repr=' II?'   <- Q3
#     13  repr=' IV'    <- Q4  (тук '?' изобщо липсва)
#
# Т.е. ФАЙЛОВЕТО е изгубило римските цифри със степенни индекси.
#
# Редът в клетките е:  ? | I? | II? | IV   = Q1 Q2 Q3 Q4
# ПЪРВАТА версия на мапирането приемаше 'IV' за "годишна"
# колона и така Q4 ИЗЧЕЗВАШЕ напълно - от 48 колони се
# получаваха 37 тримесечия. Това е уловката: "IV" е ЧЕТВЪРТОТО
# тримесечие, не пето нещо.
#
# Затова знакът '?' се третира като НЕЗНАЧИМ и се маха, а
# празната клетка се приема за първо тримесечие (както е
# в самия файл).

_QUARTER_LETTERS = {
    "": "I",      # ' ?'  -> Q1
    "I": "II",    # ' I?' -> Q2
    "II": "III",  # ' II?'-> Q3
    "IV": "IV",   # ' IV' -> Q4
    "III": "IV",  # вариант със запазена степен
}


def _quarter_roman(value: str) -> Optional[str]:
    """
    Превръща клетка от реда с тримесечия в римска цифра.

    ' ?'   -> 'I'    (Q1)
    ' I?'  -> 'II'   (Q2)
    ' II?' -> 'III'  (Q3)
    ' IV'  -> 'IV'   (Q4)
    'Q1'..'Q4' -> съответните

    ВРЪЩА None за всичко друго.

    ЗАЩО НЕ Е `re.sub("[^A-Za-z]", "")` навсякъде:
    'месец' -> "M" (главна буква) пък 'Q' липсва ->
    празен низ -> тракван като Q1. Затова празният резултат
    се валидира СРЕДУЩУЩИТЕ букви, не се приема автоматично.
    """
    raw = (value or "").strip()

    # Q1/Q2/Q3/Q4 - проверяваме ПРЕДИ почистването
    match = re.fullmatch(r"[Qq]\s*([1-4])", raw)
    if match:
        return ["I", "II", "III", "IV"][
            int(match.group(1)) - 1
        ]

    # Маха '?', интервали, точки - остават само буквите
    letters = re.sub(r"[^A-Za-z]", "", raw).upper()

    if letters == "":
        # ' ?'   -> първо тримесечие (степенният индекс е '?')
        # ''     -> първо тримесечие
        # 'месец' -> НЕ е тримесечие въпреки че след
        #            почистването остава 'M'
        if re.search(r"[A-Za-zА-Яа-я]", raw):
            return None
        # Допустими са САМО знаците, които НСИ използва за
        # изгубения степенен индекс и празното. Всичко друго
        # ('(%)', 'Total', 'месец') води до None.
        if raw and set(raw) - set("? \t "):
            return None
        return "I"

    return _QUARTER_LETTERS.get(letters)


_ROMAN_TO_QUARTER = {
    "I": "Q1",
    "II": "Q2",
    "III": "Q3",
    "IV": "Q4",
}


def _quarter_roman_to_quarter(roman: str) -> Optional[str]:
    """'I'->'Q1', 'II'->'Q2', 'III'->'Q3', 'IV'->'Q4'."""
    return _ROMAN_TO_QUARTER.get(roman)


def parse_timeseries_xlsx(
    payload: bytes,
) -> Dict[str, Any]:
    """
    Чете широк XLSX time series на НСИ -> LONG записи.

    Разпознава структурата по КОНТЕНТ:
      * ред, чиято клетка A съдържа заглавие на индикатор;
      * ред с поне 3 клетки, съвпадащи с година;
      * непосредствено под него ред с тримесечия.

    Връща {'format', 'title', 'records', 'series',
            'diagnostics', 'layout'}.
    """
    diagnostics: List[str] = []

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "records": [],
            "diagnostics": [f"Not a valid XLSX: {error}"],
        }

    shared: List[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ElementTree.fromstring(
            archive.read("xl/sharedStrings.xml")
        )
        for item in root.findall("main:si", _XL_NS):
            shared.append("".join(
                node.text or ""
                for node in item.iter(
                    "{http://schemas.openxmlformats.org/"
                    "spreadsheetml/2006/main}t"
                )
            ))

    sheet_names = [
        name for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet")
    ]
    if not sheet_names:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "records": [],
            "diagnostics": ["No worksheet"],
        }

    try:
        sheet = ElementTree.fromstring(
            archive.read(sheet_names[0])
        )
    except (ElementTree.ParseError, KeyError) as error:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "records": [],
            "diagnostics": [f"Sheet unreadable: {error}"],
        }

    # TAG: BUILD GRID
    grid: List[Dict[str, str]] = []
    for row_node in sheet.iter(
        "{http://schemas.openxmlformats.org/"
        "spreadsheetml/2006/main}row"
    ):
        cells: Dict[int, str] = {}
        for cell in row_node:
            if not cell.tag.endswith("}c"):
                continue
            reference = cell.get("r") or ""
            cell_type = cell.get("t") or "n"
            value_node = cell.find(
                "{http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main}v"
            )
            inline_node = cell.find(
                "{http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main}is"
            )
            if cell_type == "inlineStr" and inline_node is not None:
                text = "".join(
                    n.text or ""
                    for n in inline_node.iter(
                        "{http://schemas.openxmlformats.org/"
                        "spreadsheetml/2006/main}t"
                    )
                )
            elif value_node is not None:
                text = _cell_text(value_node.text, shared)
            else:
                text = ""
            cells[_column_letters(reference)] = text
        if cells:
            grid.append(cells)

    if not grid:
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "records": [],
            "diagnostics": ["Empty sheet"],
        }

    # TAG: LOCATE THE YEAR ROW
    year_row = None
    for row in grid:
        years = [
            text for text in row.values()
            if _looks_like_year(text)
        ]
        if len(years) >= 3:
            year_row = row
            break

    if year_row is None:
        diagnostics.append(
            "No year header row - not a wide time series"
        )
        return {
            "format": NsiFormat.XLSX,
            "title": None,
            "records": [],
            "diagnostics": diagnostics,
        }

    year_index = grid.index(year_row)
    quarter_row = (
        grid[year_index + 1] if year_index + 1 < len(grid) else {}
    )

    title = (
        grid[0].get(0, "").strip() if grid and 0 in grid[0] else None
    )
    if title:
        diagnostics.append(f"Title row: {title[:80]}")

    # TAG: MAP COLUMN -> PERIOD
    #
    # Годината стои само в първата клетка на всяка група от 4
    # тримесечия (D=2015, H=2016, L=2017, ...) и трябва да се
    # "разпространи" надясно до края на групата.
    period_columns: Dict[int, str] = {}
    current_year = None
    annual_count = 0

    # ВАЖНО: обхождаме ВСИЧКИ колони, а не само тези, в
    # които има година. Годината стои само в първата клетка на
    # групата от 4 (D=2015, H=2016, ...), а E/F/G са празни
    # в реда с години и трябва да наследят 2015.
    #
    # Обхождането само на year_row даваше 12 периода вместо
    # 48 - тоест само първото тримесечие на всяка година.
    max_column = 0
    for row in grid:
        if row:
            max_column = max(max_column, max(row))

    # ------------------------------------------------------------
    # TAG: HEADER SPAN ALIGNMENT
    #
    # Открито на живо 28.09.2026: header редът НЕ е плътен.
    # Първата 2015 група е D,E,F,G но има и колона C с текст.
    # При преброяване позициите се разминават с колоните и
    # Q4-овете се пренасяха.
    #
    # Затова се брои РАЗСТОЯНИЕТО между две години, а не
    # позицията в списъка. Първата група започва от първата
    # колона >= колоната на първата година.
    # ------------------------------------------------------------
    year_columns = sorted(
        column
        for column, text in year_row.items()
        if _looks_like_year(text)
    )

    if not year_columns:
        return {
            "format": NsiFormat.XLSX,
            "title": title,
            "records": [],
            "diagnostics": diagnostics + ["No year columns"],
        }

    # Къде започва първата група. Това е първата колона
    # вдясно от колоната с първата година, която е след
    # колоните с етикетите (код / тип / име на регион).
    label_columns = {
        column
        for column, text in year_row.items()
        if column < year_columns[0]
    }
    first_data_column = year_columns[0]

    for position, column in enumerate(year_columns):
        digits = re.sub(r"\D", "", year_row[column])
        year = digits[:4] if len(digits) >= 4 else digits

        if position + 1 < len(year_columns):
            span = year_columns[position + 1] - column
        else:
            # Последната година - до края на quarter_row.
            tail = [
                c for c in quarter_row
                if c > column
            ]
            span = (max(tail) - column + 1) if tail else 4

        diagnostics.append(
            f"{year}: span {span} column(s)"
        )

        for offset in range(span):
            target = column + offset
            if target > max_column:
                break

            # Първата клетка на всяка група носи ГОДИНАТА.
            # Въпреки че нейният тримечен етикет липсва или е
            # празен, тя е Q1. Първата версия пропускаше
            # тази клетка и губеше 324 записа (Q1 за
            # всички години).
            if offset == 0:
                period_columns[target] = f"{year}-Q1"
                continue

            roman = _quarter_roman(quarter_row.get(target, ""))
            quarter = (
                _quarter_roman_to_quarter(roman)
                if roman else None
            )

            if quarter:
                period_columns[target] = f"{year}-{quarter}"

    if not period_columns:
        return {
            "format": NsiFormat.XLSX,
            "title": title,
            "records": [],
            "diagnostics": diagnostics + ["No period columns"],
        }

    diagnostics.append(
        f"Period columns: {len(period_columns)} "
        f"({min(period_columns.values())} .. "
        f"{max(period_columns.values())})"
    )

    # TAG: FORWARD-FILL THE ROW LABELS
    #
    # В реалния файл "Code" и "Type of purchase" са написани
    # само на първия ред от всяка група, а "Statistical zones..."
    # стои само на реда на региона. Без разпространяване
    # надолу 2/3 от редовете биха останали без име.
    code: str = ""
    purchase_type: str = ""

    records: List[Dict[str, Any]] = []
    skipped = 0

    for row in grid[year_index + 2:]:
        geography = row.get(2, "").strip()

        if row.get(0, "").strip():
            code = row.get(0, "").strip()
        if row.get(1, "").strip():
            purchase_type = row.get(1, "").strip()

        if not geography:
            skipped += 1
            continue

        for column, period in period_columns.items():
            raw = row.get(column, "").strip()
            if not raw:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            records.append({
                "code": code,
                "purchase_type": purchase_type,
                "geography": geography,
                "period": period,
                "value": round(value, 4),
                "unit": "index_2025_eq_100",
            })

    if skipped:
        diagnostics.append(
            f"Skipped {skipped} row(s) without a geography label "
            f"(subtotals, separators)"
        )

    diagnostics.append(f"Emitted {len(records)} long-format records")

    return {
        "format": NsiFormat.XLSX,
        "title": title,
        "layout": "wide_timeseries_long_output",
        "series": [
            {"code": name, "name": name}
            for name in (
                "code", "purchase_type", "geography",
                "period", "value", "unit",
            )
        ],
        "records": records,
        "diagnostics": diagnostics,
    }


# TAG: UNIFIED DISPATCHER

def _looks_flat_and_useless(
    result: Dict[str, Any],
) -> bool:
    """
    Плоският парсер се провали, ако заглавията са измислени.

    Признак: поне половината колони се казват "col_N" - тоест
    не е открит реален заглавен ред. Това е точно каквото се
    случи с реалния HPI_2.1-en.xlsx на 28.09.2026.
    """
    series = result.get("series") or []
    if not series:
        return True

    generic = sum(
        1
        for item in series
        if str(item.get("name", "")).startswith("col_")
    )

    return generic * 2 >= len(series)


def parse_nsi_file(
    filename: str,
    payload: bytes,
) -> Dict[str, Any]:
    """
    Разпознава формата и извиква съответния парсер.

    Връща структурата, която normalizer очаква, плюс
    'attributes' и 'source' полета за директно подаване
    към unified schema.
    """
    head = payload[:4096]
    detected = detect_format(filename, head)

    if detected == NsiFormat.REGNP:
        result = parse_regnp(payload)
    elif detected == NsiFormat.XLSX:
        result = parse_xlsx(payload)
        # Широк time series има смислена геометрия (години в
        # колоните). Ако плоският парсер не намери смислени
        # заглавия, пробваме широкия - той е строго по-информативен
        # и за обикновена таблица няма какво да загуби.
        if _looks_flat_and_useless(result):
            wide = parse_timeseries_xlsx(payload)
            if wide["records"]:
                wide["diagnostics"] = (
                    ["Flat parser produced column-N headers; "
                     "re-parsed as wide time series."]
                    + wide["diagnostics"]
                )
                wide["format"] = result["format"]
                result = wide
    elif detected == NsiFormat.CSV:
        result = parse_csv(payload)
    else:
        return {
            "format": NsiFormat.UNKNOWN,
            "title": None,
            "series": [],
            "records": [],
            "record_count": 0,
            "diagnostics": [
                f"Unrecognised format for {filename!r}. "
                f"First bytes: {head[:24]!r}. Raw file is kept "
                f"in storage_raw - nothing is lost."
            ],
        }

    # Плоските парсери не винаги връщат diagnostics. Липсата
    # на обяснение при НУЛЕВ резултат е най-лошият вид
    # повреда - изглежда, че е проверено и наистина е нямало
    # нищо, а всъщност форматът просто не е разпознат.
    if not result.get("diagnostics"):
        if result.get("records"):
            result["diagnostics"] = [
                f"Parsed as {result.get('format')} with "
                f"{len(result['records'])} record(s)."
            ]
        else:
            result["diagnostics"] = [
                f"Detected as {result.get('format')} but found no "
                f"usable rows. Possibly a layout variant not yet "
                f"supported. Raw file kept in storage_raw - "
                f"nothing is lost."
            ]

    result["source"] = "nsi"
    result["source_file"] = filename
    result["record_count"] = len(result["records"])

    return result


def to_unified_records(
    parsed: Dict[str, Any],
    indicator_id: str,
    dataset_name: str = "",
) -> List[Dict[str, Any]]:
    """
    Превръща разпарсените редове в unified schema записи.

    Ключово: НСИ данните са АГРЕГАТИ (индекси, средни стойности
    по райони), а не имотни обяви. Затова всеки запис се
    маркира изрично с 'record_kind': 'aggregate' и 'geography',
    за да не бъде объркан от агент за имот.
    """
    records: List[Dict[str, Any]] = []

    for position, row in enumerate(parsed.get("records", [])):
        record = {
            "source": "nsi",
            "dataset_id": indicator_id,
            "record_kind": "aggregate",
            "attributes": dict(row),
        }

        geography = (
            row.get("Район")
            or row.get("Област")
            or row.get("Населено място")
            or row.get("TERITORY")
            or row.get("geo")
        )
        if geography:
            record["geography"] = str(geography).strip()

        period = (
            row.get("Период")
            or row.get("Година")
            or row.get("Месец")
            or row.get("TIME")
        )
        if period:
            record["period"] = str(period).strip()

        value = None
        for key in (
            "Стойност",
            "Индекс",
            "Value",
            "value",
        ):
            if key in row and str(row[key]).strip():
                value = row[key]
                break
        if value is not None:
            record["value_raw"] = str(value).strip()

        records.append(record)

    return records
