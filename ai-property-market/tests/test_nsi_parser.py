# TAG: TESTS - NSI PARSER
#
# Всички тестове използват СИНТЕТИЧНИ файлове, построени
# тук. Няма мрежа. Това е умишлено: преди да се тръгне да се
# пита реалният НСИ, трябва да е доказано какво може и какво
# не може да се прочете - и то БЕЗ да зависи от това какъв
# файл НСИ ще публикува утре.
#
# Стойността на файла: когато дойде първият реален .regnp,
# ако не се прочете, веднага се знае дали проблемът е в
# формата (тогава тук трябва нов case) или в самия файл.

import io
import zipfile

import pytest

from tools.nsi_parser import (
    NsiFormat,
    detect_format,
    parse_csv,
    parse_nsi_file,
    parse_regnp,
    parse_xlsx,
    to_unified_records,
)


# ============================================================
# TAG: FORMAT DETECTION
# ============================================================

def test_detects_regnp_by_extension():
    assert detect_format("hpi_2026.regnp", b"") == NsiFormat.REGNP


def test_detects_xlsx_by_extension():
    assert detect_format("prices.xlsx", b"") == NsiFormat.XLSX


def test_detects_csv_by_extension():
    assert detect_format("data.csv", b"") == NsiFormat.CSV


def test_detects_xlsx_by_magic_bytes():
    """
    Файл без информативно разширение, но ZIP сигнатура.
    """
    assert (
        detect_format("download", b"PK\x03\x04rest") == NsiFormat.XLSX
    )


def test_detects_regnp_by_content():
    assert (
        detect_format("data", b"<REGNP><OBS/></REGNP>")
        == NsiFormat.REGNP
    )


def test_unknown_format_returns_unknown():
    assert (
        detect_format("mystery", b"\x00\x01\x02binary")
        == NsiFormat.UNKNOWN
    )


# ============================================================
# TAG: REGNP
# ============================================================

REGNP_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<REGNP>
  <META>
    <TITLE>Индекси на цените на жилищата</TITLE>
    <NAME>HPI</NAME>
  </META>
  <SERIES_DEF_ALL>
    <SER_DIM DIM_CODE="S_A01">
      <SER_DIM_LABELS>
        <SER_DIM_LABEL>Район</SER_DIM_LABEL>
      </SER_DIM_LABELS>
    </SER_DIM>
    <SER_DIM DIM_CODE="S_PERIOD">
      <SER_DIM_LABELS>
        <SER_DIM_LABEL>Период</SER_DIM_LABEL>
      </SER_DIM_LABELS>
    </SER_DIM>
  </SERIES_DEF_ALL>
  <OBS>
    <SERIES_DEF>S_A01</SERIES_DEF>
    <VALS>
      <OBS_VALUE>0</OBS_VALUE>
      <OBS_VALUE>София-град</OBS_VALUE>
      <OBS_VALUE>2026-06</OBS_VALUE>
    </VALS>
  </OBS>
  <OBS>
    <SERIES_DEF>S_A01</SERIES_DEF>
    <VALS>
      <OBS_VALUE>0</OBS_VALUE>
      <OBS_VALUE>Пловдив</OBS_VALUE>
      <OBS_VALUE>2026-06</OBS_VALUE>
    </VALS>
  </OBS>
</REGNP>
""".encode("utf-8")


def test_regnp_parses_title():
    result = parse_regnp(REGNP_SAMPLE)

    assert result["title"] == "Индекси на цените на жилищата"


def test_regnp_parses_series_names():
    result = parse_regnp(REGNP_SAMPLE)

    names = {s["name"] for s in result["series"]}
    assert "Район" in names
    assert "Период" in names


def test_regnp_parses_observations():
    result = parse_regnp(REGNP_SAMPLE)

    assert len(result["records"]) == 2
    assert result["records"][0]["Район"] == "София-град"
    assert result["records"][0]["Период"] == "2026-06"


def test_regnp_skips_series_index_column():
    """
    <SERIES_DEF> означава, че първата стойност е индексът на
    измерението, а не данни. Ако не се пропуска, всички
    записи получават "0" в първата колона.
    """
    result = parse_regnp(REGNP_SAMPLE)

    first_values = [
        value
        for record in result["records"]
        for value in record.values()
    ]
    assert "0" not in first_values


def test_regnp_on_broken_xml_reports_diagnostic():
    result = parse_regnp(b"<REGNP><unclosed>")

    assert result["records"] == []
    assert result["diagnostics"]
    assert "parse error" in result["diagnostics"][0].lower()


def test_regnp_on_empty_root_reports_diagnostic():
    result = parse_regnp(b"<REGNP></REGNP>")

    assert result["records"] == []
    assert result["diagnostics"]


# ============================================================
# TAG: XLSX
# ============================================================

def _build_xlsx(rows, sheet_name="xl/worksheets/sheet1.xml"):
    """
    Минимален валиден XLSX с една таблица и inline strings.
    Целта е да се тества ЧЕТЕНЕТО, не създаването на Excel.
    """
    cells = []
    for row_index, row in enumerate(rows, start=1):
        parts = []
        for col_index, value in enumerate(row):
            letter = chr(ord("A") + col_index)
            reference = f"{letter}{row_index}"
            if value is None or value == "":
                continue
            parts.append(
                f'<c r="{reference}" t="inlineStr">'
                f"<is><t>{value}</t></is></c>"
            )
        if parts:
            cells.append(
                f'<row r="{row_index}">{"".join(parts)}</row>'
            )

    sheet_xml = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData>'
        "</worksheet>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(sheet_name, sheet_xml)
    return buffer.getvalue()


def test_xlsx_parses_records():
    payload = _build_xlsx([
        ["Район", "Период", "Индекс"],
        ["София-град", "2026-06", "145.2"],
        ["Пловдив", "2026-06", "132.8"],
    ])

    result = parse_xlsx(payload)

    assert len(result["records"]) == 2
    assert result["records"][0]["Район"] == "София-град"
    assert result["records"][0]["Индекс"] == "145.2"


def test_xlsx_skips_leading_title_rows():
    """
    Реалните отчети на НСИ започват със заглавие и период
    ПРЕДИ таблицата. Наивното "вземи първия ред" би дало
    безсмислени колони.
    """
    payload = _build_xlsx([
        ["Индекси на цените на жилищата - юни 2026", "", ""],
        ["", "", ""],
        ["Район", "Период", "Индекс"],
        ["София-град", "2026-06", "145.2"],
    ])

    result = parse_xlsx(payload)

    assert "Район" in result["records"][0]
    assert result["records"][0]["Район"] == "София-град"


def test_xlsx_skips_blank_rows():
    payload = _build_xlsx([
        ["Район", "Индекс"],
        ["София-град", "145.2"],
        ["", "", ""],
        ["Пловдив", "132.8"],
    ])

    result = parse_xlsx(payload)

    assert len(result["records"]) == 2


def test_xlsx_makes_column_names_unique():
    """
    Празна колона за единици би дала два еднакви ключа и
    едната да презапише другата.
    """
    payload = _build_xlsx([
        ["Район", "Индекс", "", "Брой"],
        ["София", "145.2", "100, 150", "7"],
    ])

    result = parse_xlsx(payload)

    keys = list(result["records"][0].keys())
    assert len(keys) == len(set(keys))
    assert "col_2" in keys


def test_xlsx_on_broken_archive_reports_diagnostic():
    result = parse_xlsx(b"not a zip at all")

    assert result["records"] == []
    assert result["diagnostics"]


def test_xlsx_on_empty_sheet_reports_diagnostic():
    payload = _build_xlsx([])
    result = parse_xlsx(payload)

    assert result["records"] == []
    assert result["diagnostics"]


# ============================================================
# TAG: CSV
# ============================================================

def test_csv_semicolon_is_detected():
    result = parse_csv(
        "Район;Период;Индекс\n"
        "София-град;2026-06;145.2\n"
        .encode("utf-8")
    )

    assert result["records"][0]["Район"] == "София-град"
    assert result["records"][0]["Индекс"] == "145.2"


def test_csv_comma_is_detected():
    result = parse_csv(
        b"region,period,index\nSofia,2026-06,145.2\n"
    )

    assert result["records"][0]["region"] == "Sofia"


def test_csv_handles_cp1251():
    """
    НСИ често публикува Windows кодировка за стари отчети.
    """
    payload = "Район;Индекс\nСофия;145.2\n".encode("cp1251")
    result = parse_csv(payload)

    assert result["records"][0]["Район"] == "София"


def test_csv_reports_diagnostics():
    result = parse_csv(b"a;b;c\n1;2;3\n")

    assert result["diagnostics"]


def test_csv_on_empty_returns_nothing():
    result = parse_csv(b"")

    assert result["records"] == []


# ============================================================
# TAG: DISPATCHER
# ============================================================

def test_dispatch_regnp():
    result = parse_nsi_file("hpi.regnp", REGNP_SAMPLE)

    assert result["format"] == NsiFormat.REGNP
    assert result["source"] == "nsi"
    assert result["record_count"] == 2


def test_dispatch_unknown_keeps_raw():
    result = parse_nsi_file(
        "mystery.dat", b"\x00\x01\x02"
    )

    assert result["format"] == NsiFormat.UNKNOWN
    assert result["diagnostics"]
    # Нищо не се измисля.
    assert result["records"] == []


def test_dispatch_always_returns_diagnostics_key():
    for name, payload in (
        ("a.regnp", REGNP_SAMPLE),
        ("a.csv", b"a,b\n1,2\n"),
    ):
        assert "diagnostics" in parse_nsi_file(name, payload)


# ============================================================
# TAG: UNIFIED SCHEMA
# ============================================================

def test_to_unified_marks_records_as_aggregate():
    """
    КРИТИЧНО: НСИ данните не са имотни обяви. Ако агентът
    разчете HPI индекс като "цена на имот", продуктът ще
    даде безсмислен отговор. Затова record_kind е изричен.
    """
    parsed = parse_regnp(REGNP_SAMPLE)
    records = to_unified_records(parsed, "90")

    assert all(
        r["record_kind"] == "aggregate" for r in records
    )
    assert all(r["source"] == "nsi" for r in records)


def test_to_unified_extracts_geography_and_period():
    parsed = parse_regnp(REGNP_SAMPLE)
    records = to_unified_records(parsed, "90")

    assert records[0]["geography"] == "София-град"
    assert records[0]["period"] == "2026-06"


def test_to_unified_keeps_original_attributes():
    parsed = parse_regnp(REGNP_SAMPLE)
    records = to_unified_records(parsed, "90")

    assert "Район" in records[0]["attributes"]


def test_to_unified_never_invents_geography():
    parsed = parse_regnp(b"<REGNP></REGNP>")
    records = to_unified_records(parsed, "90")

    assert records == []


def test_to_unified_extracts_value_when_present():
    parsed = parse_csv(
        "Район;Индекс\nСофия;145.2\n".encode("utf-8")
    )
    records = to_unified_records(parsed, "99")

    assert records[0]["value_raw"] == "145.2"


def test_to_unified_handles_empty_input():
    assert to_unified_records({"records": []}, "1") == []
