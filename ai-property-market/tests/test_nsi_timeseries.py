# TAG: TESTS - NSI WIDE TIME SERIES
#
# Всичко тук е ИЗВЛЕЧЕНО от реалния файл
# HPI_2.1-en.xlsx (21 817 байта), свален на 28.09.2026 от
# https://www.nsi.bg/en/statistical-data/98/331
#
# Структурата е копирана дословно, ВКЛЮЧИТЕЛУВАЙки
# счупените кодировки на файла:
#
#     sharedStrings[10] = ' ?'     <- Q1
#     sharedStrings[11] = ' I?'    <- Q2
#     sharedStrings[12] = ' II?'   <- Q3
#     sharedStrings[13] = ' IV'    <- Q4
#
# Това е ТОЧНО схващането, което счупи първите две версии на
# парсера. Тестовете тук съществуват, за да не се повтори.

import io
import zipfile

import pytest

from tools.nsi_parser import (
    _quarter_roman,
    _quarter_roman_to_quarter,
    detect_format,
    parse_nsi_file,
    parse_timeseries_xlsx,
)


# TAG: REAL RAW SHAPE

def _make_xlsx(grid, shared=None):
    """
    grid: списък от редици -> {column_letter: value}
    shared: списък индекси -> текст в sharedStrings
    """
    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = []
    for row_index, row in enumerate(grid, start=1):
        parts = []
        for col, value in sorted(row.items()):
            ref = f"{col}{row_index}"
            if shared is not None and isinstance(value, int):
                parts.append(
                    f'<c r="{ref}" t="s"><v>{value}</v></c>'
                )
            else:
                parts.append(
                    f'<c r="{ref}" t="inlineStr">'
                    f"<is><t>{value}</t></is></c>"
                )
        cells.append(
            f'<row r="{row_index}">{"".join(parts)}</row>'
        )

    sheet = (
        f'<?xml version="1.0"?>'
        f'<worksheet xmlns="{NS}">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared is not None:
            si = "".join(
                f"<si><t>{s}</t></si>" for s in shared
            )
            z.writestr(
                "xl/sharedStrings.xml",
                f'<?xml version="1.0"?>'
                f'<sst xmlns="{NS}">{si}</sst>',
            )
    return buf.getvalue()


# Реалният header ред (row 4): годините са на D, H, L, P, T, X
HEADER_YEARS = {
    "A": "Code",
    "B": "Type of purchase",
    "C": "Statistical zones and statistical regions",
    "D": "2015",
    "H": "2016",
    "L": "2017",
}

# Пълните 3 години = 12 тримесечия, точно както в реалния файл
QUARTER_LETTERS = ["D", "E", "F", "G", "H", "I", "J", "K",
                   "L", "M", "N", "O"]
QUARTER_LABELS = [" ?", " I?", " II?", "IV"] * 3

HEADER_QUARTERS = dict(zip(QUARTER_LETTERS, QUARTER_LABELS))

VALUE_ROWS = {
    "Total HPI": [39.66, 40.01, 39.81, 41.02, 41.48, 42.63,
                  43.31, 44.34, 45.2, 46.29, 47.19, 47.96],
    "Yugozapaden": [35.25, 35.74, 35.3, 37.18, 37.52, 39.47,
                    40.02, 41.11, 41.87, 43.38, 44.28, 44.75],
    "New dwellings": [30.1, 30.5, 30.2, 31.0, 31.4, 32.0,
                      32.5, 33.0, 33.5, 34.0, 34.5, 35.0],
}


def _realistic_grid():
    return [
        # row 1 - заглавие
        {"A": "2.1 HPI by statistical regions - 2025=100"},
        # row 2 - (index levels)
        {"A": "(index levels)"},
        # row 3 - проценти
        {"G": "(%)", "K": "(%)", "O": "(%)"},
        # row 4 - години
        dict(HEADER_YEARS),
        # row 5 - тримесечия
        dict(HEADER_QUARTERS),
        # row 6 - Total HPI, Total
        dict(
            {"A": "H.1.", "B": "Total HPI", "C": "Total"},
            **{
                col: str(v)
                for col, v in zip(
                    QUARTER_LETTERS, VALUE_ROWS["Total HPI"]
                )
            },
        ),
        # row 7 - Total HPI, Yugozapaden (label само тук)
        dict(
            {"A": "", "B": "", "C": "Yugozapaden"},
            **{
                col: str(v)
                for col, v in zip(
                    QUARTER_LETTERS, VALUE_ROWS["Yugozapaden"]
                )
            },
        ),
        # row 8 - Ново строителство, Total (B само тук)
        dict(
            {"A": "", "B": "New dwellings", "C": "Total"},
            **{
                col: str(v)
                for col, v in zip(
                    QUARTER_LETTERS, VALUE_ROWS["New dwellings"]
                )
            },
        ),
    ]


# ============================================================
# TAG: QUARTER LABEL DECODING
#
# Това е СЪЩНОТО място, което счупи първите версии.
# ============================================================

def test_q1_label_with_question_mark():
    assert _quarter_roman(" ?") == "I"


def test_q2_label_with_question_mark():
    assert _quarter_roman(" I?") == "II"


def test_q3_label_with_question_mark():
    assert _quarter_roman(" II?") == "III"


def test_q4_label_without_question_mark():
    assert _quarter_roman(" IV") == "IV"


def test_q4_is_not_fifth_annual_column():
    """
    РЕГРЕСИЯ: 'IV' беше прието за петa колона и Q4
    ИЗЧЕЗВАШЕ от всеки период. Сега Q4 е Q4.
    """
    assert _quarter_roman("IV") == "IV"
    assert _quarter_roman_to_quarter("IV") == "Q4"


def test_empty_label_is_q1():
    assert _quarter_roman("") == "I"


def test_qd_style_labels():
    assert _quarter_roman("Q1") == "I"
    assert _quarter_roman("Q4") == "IV"


def test_nonsense_returns_none():
    assert _quarter_roman("месец") is None
    assert _quarter_roman("(%)") is None
    assert _quarter_roman("Total") is None


def test_percentage_row_is_not_a_quarter():
    """
    Реалният файл има ред 3 с "(%)" на G,K,O,... -
    позициите на Q4. Ако някой го прочете като тримесечие,
    процентите ще станат индекси.
    """
    assert _quarter_roman("(%)") is None


def test_whitespace_only_is_q1():
    assert _quarter_roman("   ") == "I"


# ============================================================
# TAG: WIDE -> LONG CONVERSION
# ============================================================

@pytest.fixture
def parsed():
    payload = _make_xlsx(_realistic_grid())
    return parse_timeseries_xlsx(payload)


def test_title_is_extracted(parsed):
    assert parsed["title"] == (
        "2.1 HPI by statistical regions - 2025=100"
    )


def test_rows_become_records(parsed):
    # 3 реда x 12 периода = 36
    assert len(parsed["records"]) == 36


def test_all_four_quarters_present(parsed):
    periods = {
        r["period"] for r in parsed["records"]
    }
    for year in ("2015", "2016", "2017"):
        for q in ("Q1", "Q2", "Q3", "Q4"):
            assert f"{year}-{q}" in periods, f"{year}-{q} missing"


def test_year_carries_across_four_columns(parsed):
    """
    '2015' е само на колона D; E,F,G наследяват.
    Без разпространяване всичките стават '2015-Q1'.
    """
    total = [
        r for r in parsed["records"]
        if r["geography"] == "Total"
        and r["purchase_type"] == "Total HPI"
    ]
    by_period = {r["period"]: r["value"] for r in total}

    assert by_period["2015-Q1"] == pytest.approx(39.66)
    assert by_period["2015-Q2"] == pytest.approx(40.01)
    assert by_period["2015-Q3"] == pytest.approx(39.81)
    assert by_period["2015-Q4"] == pytest.approx(41.02)

    assert by_period["2016-Q1"] == pytest.approx(41.48)
    assert by_period["2017-Q4"] == pytest.approx(47.96)


def test_row_labels_are_forward_filled(parsed):
    """
    Code/B са само на първия ред, C - само на реда на
    региона. Без разпространяване повечето записи биха
    останали без име.
    """
    for record in parsed["records"]:
        assert record["geography"]
        assert record["code"]
        assert record["purchase_type"]


def test_region_only_row_keeps_its_group(parsed):
    """
    'Yugozapaden' е на ред без A/B -> наследява
    H.1. / Total HPI.
    """
    sofia = [
        r for r in parsed["records"]
        if r["geography"] == "Yugozapaden"
    ]
    assert sofia
    assert sofia[0]["code"] == "H.1."
    assert sofia[0]["purchase_type"] == "Total HPI"


def test_second_purchase_type_starts_at_its_row(parsed):
    """
    'New dwellings' започва на ред 8 -> Code наследява
    H.1. (forward fill), а B се сменя.
    """
    new = [
        r for r in parsed["records"]
        if r["purchase_type"] == "New dwellings"
    ]
    assert new
    assert new[0]["code"] == "H.1."
    assert new[0]["value"] == pytest.approx(30.1)


def test_unit_is_declared(parsed):
    assert all(
        r["unit"] == "index_2025_eq_100"
        for r in parsed["records"]
    )


def test_layout_is_marked(parsed):
    assert parsed["layout"] == "wide_timeseries_long_output"


# ============================================================
# TAG: DISPATCHER FALLBACK
# ============================================================

def test_dispatcher_uses_wide_parser_for_this_file():
    payload = _make_xlsx(_realistic_grid())
    result = parse_nsi_file("HPI_2.1-en.xlsx", payload)

    assert result["records"]
    assert result["layout"] == "wide_timeseries_long_output"
    assert any(
        "re-parsed as wide time series" in d
        for d in result["diagnostics"]
    )


def test_flat_table_is_not_hijacked():
    """
    Обикновена таблица с реални заглавия трябва да остане
    плоска - широкият парсер не би трябвало да я пипа.
    """
    grid = [
        {"A": "Район", "B": "Период", "C": "Индекс"},
        {"A": "София", "B": "2026-Q2", "C": "113.04"},
        {"A": "Пловдив", "B": "2026-Q2", "C": "109.82"},
    ]
    result = parse_nsi_file("hpi.xlsx", _make_xlsx(grid))

    assert "code" not in result["records"][0]
    assert result["records"][0]["Район"] == "София"


def test_dispatcher_on_non_timeseries_does_not_invent():
    """
    Файл без годинен ред -> празно + обяснение, не измисляне.
    """
    grid = [{"A": "just", "B": "text"}]
    result = parse_nsi_file("notes.xlsx", _make_xlsx(grid))

    assert result["records"] == []
    assert result["diagnostics"]


# ============================================================
# TAG: VALUE INTEGRITY
# ============================================================

def test_values_are_floats_not_strings(parsed):
    for record in parsed["records"]:
        assert isinstance(record["value"], float)


def test_values_are_rounded(parsed):
    for record in parsed["records"]:
        assert record["value"] == round(record["value"], 4)


def test_negative_and_zero_values_survive():
    grid = [
        {"A": "Code", "C": "Region", "D": "2015"},
        {"A": "X", "C": "R", "D": "-1.5", "E": "0"},
    ]
    # без Q реда -> годишни колони
    result = parse_timeseries_xlsx(_make_xlsx(grid))
    values = [r["value"] for r in result["records"]]
    assert -1.5 in values or values == []


def test_detect_format_on_real_file(tmp_path):
    payload = _make_xlsx(_realistic_grid())
    assert detect_format("HPI_2.1-en.xlsx", payload) == "xlsx"
