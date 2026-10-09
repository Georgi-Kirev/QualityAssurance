# TAG: TESTS - NSI COLLECTOR
#
# Без мрежа. Проверява се ЗАЩО ГРАНИЦАТА СА ТАКИВА - domain
# guard-ът е единственото, което пази дали колекторът няма
# да свали файл от чужд домейн по грешка или злонамерено.

import json

import pytest

from collector.nsi import NsiCollector


@pytest.fixture
def collector(tmp_path):
    return NsiCollector(tmp_path)


# ============================================================
# TAG: STRUCTURE
# ============================================================

def test_creates_source_folder(collector, tmp_path):
    assert (tmp_path / "nsi").is_dir()


def test_source_name_is_nsi(collector):
    assert collector.SOURCE_NAME == "nsi"


def test_uses_official_domain(collector):
    assert collector.BASE_URL == "https://www.nsi.bg"


# ============================================================
# TAG: DOMAIN GUARD
# ============================================================

def test_allows_official_host(collector):
    collector._assert_allowed(
        "https://www.nsi.bg/fileadmin/x.xlsx"
    )


def test_allows_apex_host(collector):
    collector._assert_allowed("https://nsi.bg/x.xlsx")


@pytest.mark.parametrize("host", [
    "evil.com",
    "nsi.bg.evil.com",
    "www.nsi.bg.evil.com/x.xlsx",
    "notnsi.bg",
    "",
])
def test_rejects_foreign_hosts(collector, host):
    """
    КЛЮЧОВИЯТ тест. Без тази проверка колекторът може да
    свали произволен файл, ако някой сгреши адреса.
    """
    with pytest.raises(ValueError) as info:
        collector._assert_allowed(f"https://{host}/x.xlsx")

    assert "Refusing to fetch" in str(info.value)


def test_guard_error_names_allowed_hosts(collector):
    with pytest.raises(ValueError) as info:
        collector._assert_allowed("https://evil.com/x")

    assert "www.nsi.bg" in str(info.value)


# ============================================================
# TAG: POLITE CLIENT
# ============================================================

def test_has_identifying_user_agent(collector):
    """
    Държавен ресурс - трябва да се знае кой е клиентът.
    """
    agent = collector.USER_AGENT

    assert "AIPropertyMarket" in agent
    assert len(agent) > 15


def test_rate_limit_is_conservative(collector):
    """
    Държавен ресурс. 2 s между заявки е в рамките на учтивото.
    """
    assert collector.MIN_INTERVAL_SECONDS >= 1.0


def test_allowed_hosts_are_minimal(collector):
    """
    Колкото по-малко домейни, толкова по-трудно е да се
    използва като open proxy.
    """
    assert len(collector.ALLOWED_HOSTS) <= 3
    assert all("nsi" in host for host in collector.ALLOWED_HOSTS)


# ============================================================
# TAG: INDICATOR DISCOVERY (без мрежа - само структурата)
# ============================================================

def test_discovery_targets_the_verified_data_page(collector):
    """
    ПРОВЕРЕНО НА ЖИВО 28.09.2026: /en/statistical-data/98/331
    съдържа връзка към HPI_2.1-en.xlsx с реални данни.

    Страниците 90, 96 и /en/99 са МЕТАДАННИ - само текст.
    Ако адресът се върне към тях, тестът пада.
    """
    assert collector.BULGARIAN_PRICE_PAGE == (
        "/en/statistical-data/98/331"
    )
    assert collector.HOUSE_PRICE_PAGE == (
        "/en/statistical-data/98/331"
    )


def test_metadata_pages_are_remembered(collector):
    """
    Записани са, за да не се харчат заявки повторно.
    """
    assert "/statistical-data/90" in collector.METADATA_ONLY_PAGES
    assert "/en/statistical-data/99" in (
        collector.METADATA_ONLY_PAGES
    )


# ============================================================
# TAG: DISCOVERY - ДИАГНОСТИКА
#
# Проверено на живо 28.09.2026: /statistical-data/90 връща
# 256 781 байта и НЯМА нито една връзка към файл с данни.
# Единствената таблица е 5 реда с дати на публикуване.
#
# Предишната версия връщаше празен списък и мълчеше - точно
# най-лошия вид повреда. Тестовете тук пазят изискването
# да се ОБЯСНВА защо не е намерено нищо.
# ============================================================

INDEX_PAGE_HTML = """
<html><head><link href="/a.css"></head><body>
  <table>
    <tr><th>Дата на публикуване</th></tr>
    <tr><td>23.12.2026 Индекси на цените на жилищата</td></tr>
    <tr><td>26.03.2027 Индекси на цените на жилищата</td></tr>
  </table>
  <a href="/templates/default/js/main.js"></a>
</body></html>
"""


# ============================================================
# TAG: XLSX FIXTURES FOR THE OFFLINE PATH
#
# Минимални валидни книги, построени в паметта. Целта е
# да се провери ОРКЕСТРАЦИЯТА (парс -> unified -> json),
# а не парсерът - за него има отделни тестове.
#
# openpyxl НЕ е зависимост на проекта: tools/nsi_parser.py
# чете XLSX сам, през zipfile + ElementTree. Затова и
# фистурата се строи на ръка, със същия формат.
# ============================================================

_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
}


def _cell_xml(reference, value):
    from xml.sax.saxutils import escape

    text = str(value)
    # Апостроф и запетая трябва да са XML-безопасни.
    escaped = escape(text).replace("'", "&apos;")
    escaped = escaped.replace('"', "&quot;")
    for char, replacement in _ESCAPES.items():
        if char != escaped[0:0] and char in text:
            escaped = escaped.replace(char, replacement)

    return (
        f'<c r="{reference}" t="inlineStr">'
        f"<is><t>{escaped}</t></is></c>"
    )


def _xlsx_bytes(rows):
    from io import BytesIO
    from zipfile import ZipFile

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(
            _cell_xml(
                f"{chr(ord('A') + column)}{row_index}",
                value,
            )
            for column, value in enumerate(row)
            if value is not None
        )
        sheet_rows.append(
            f'<row r="{row_index}">{cells}</row>'
        )

    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org'
        '/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org'
        '/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml"'
        ' ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml"'
        ' ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org'
        '/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org'
        '/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )

    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org'
        '/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships'
        '/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org'
        '/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships'
        '/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels", relationships
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml", sheet
        )

    return buffer.getvalue()


FLAT_XLSX_BYTES = _xlsx_bytes([
    ["Район", "Индекс", "Период"],
    ["София", "109.39", "2026-Q1"],
    ["Пловдив", "101.20", "2026-Q1"],
])

# Header без използваеми редове - и за двата парсера.
NO_USABLE_ROWS_XLSX_BYTES = _xlsx_bytes([
    ["Район", "Индекс", "Период"],
])


def test_discovery_reports_zero_without_silent_failure(
    collector, monkeypatch
):
    """
    РЕГРЕСИЯ: празен списък без обяснение изглежда като
    "проверих и нямаше нищо", което е лъжа.
    """
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: INDEX_PAGE_HTML.encode("utf-8"),
    )

    result = collector.discover_indicators()

    assert result["indicators_found"] == 0
    assert result["diagnostics"], (
        "zero results MUST come with a reason"
    )


def test_diagnostic_explains_metadata_only_page(
    collector, monkeypatch
):
    """
    Връзката, която наистина липсва, е разпозната като
    "само метаданни" - това е причината, установена
    емпирично, а не догадка.
    """
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: INDEX_PAGE_HTML.encode("utf-8"),
    )

    diagnostics = " ".join(
        collector.discover_indicators()["diagnostics"]
    ).lower()

    assert "metadata" in diagnostics
    assert "time series" in diagnostics


def test_relative_href_is_resolved_against_domain_root(tmp_path):
    """
    РЕГРЕСИЯ: href-ът на НСИ е
    "sites/default/files/...xlsx" и лежи в КОРЕНА на
    домейна. urljoin() спрямо текущата страница даваше
    ".../98/331/sites/..." и 404.
    """
    nsi = NsiCollector(tmp_path)
    items = nsi._parse_data_file_links(
        '<a href="sites/default/files/f/HPI.xlsx">x</a>'
    )

    assert items[0]["url"] == (
        "https://www.nsi.bg/sites/default/files/f/HPI.xlsx"
    )


def test_absolute_and_rooted_hrefs_pass_through(tmp_path):
    nsi = NsiCollector(tmp_path)
    items = nsi._parse_data_file_links(
        '<a href="https://www.nsi.bg/a.xlsx">a</a>'
        '<a href="/sites/b.xlsx">b</a>'
    )

    assert [i["url"] for i in items] == [
        "https://www.nsi.bg/a.xlsx",
        "https://www.nsi.bg/sites/b.xlsx",
    ]


def test_duplicate_hrefs_are_collapsed(tmp_path):
    nsi = NsiCollector(tmp_path)
    items = nsi._parse_data_file_links(
        '<a href="sites/f/a.xlsx">1</a>'
        '<a href="sites/f/a.xlsx">2</a>'
    )

    assert len(items) == 1


def test_successful_discovery_reports_absolute_url(collector):
    html = '<a href="sites/default/files/f/hpi.xlsx">HPI</a>'
    collector._get = lambda url: html.encode("utf-8")

    result = collector.discover_indicators()

    assert result["indicators_found"] == 1
    assert result["indicators"][0]["url"].startswith(
        "https://www.nsi.bg/"
    )
    assert any(
        "DOMAIN root" in d for d in result["diagnostics"]
    )


def test_diagnostic_measurements_real_extensions(
    collector, monkeypatch
):
    """
    Диагностиката трябва да посочи какво РЕАЛНО е на
    страницата, за да е проверимо.
    """
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: INDEX_PAGE_HTML.encode("utf-8"),
    )

    result = collector.discover_indicators()

    assert result["href_extensions"].get("css") == 1
    assert result["html_bytes"] > 0


def test_diagnostic_suggests_next_step(collector, monkeypatch):
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: INDEX_PAGE_HTML.encode("utf-8"),
    )

    diagnostics = " ".join(
        collector.discover_indicators()["diagnostics"]
    ).lower()

    assert "next step" in diagnostics


def test_successful_discovery_has_friendly_diagnostic(
    collector, monkeypatch
):
    """
    Когато файлове СА намерени, диагностиката трябва да е
    кратка и позитивна, не да повтаря съвети.
    """
    html = (
        '<a href="/f/hpi.xlsx">HPI</a>'
        '<a href="/f/rents.xls">Rents</a>'
    )
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: html.encode("utf-8"),
    )

    result = collector.discover_indicators()

    assert result["indicators_found"] == 2
    assert "Found 2" in result["diagnostics"][0]
    assert "next step" not in " ".join(
        result["diagnostics"]
    ).lower()


def test_found_urls_are_absolute(collector, monkeypatch):
    html = '<a href="/f/hpi.xlsx">HPI</a>'
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: html.encode("utf-8"),
    )

    result = collector.discover_indicators()

    assert result["indicators"][0]["url"] == (
        "https://www.nsi.bg/f/hpi.xlsx"
    )


# ============================================================
# TAG: OFFLINE RE-PARSE (без мрежа)
# ============================================================

def test_parse_local_file_never_touches_the_network(
    collector, tmp_path, monkeypatch
):
    """
    Условие на задачата: повторната обработка трябва да е
    ВЪЗМОЖНА без ново сваляне. Страхът е, че методът
    тихо прави заявка. Затова мрежата се прави невъзможна.
    """
    def explode(*args, **kwargs):
        raise AssertionError("Network access attempted")

    monkeypatch.setattr(collector, "_get", explode)
    monkeypatch.setattr(
        collector, "fetch_indicator_file", explode
    )

    source = tmp_path / "flat.xlsx"
    source.write_bytes(FLAT_XLSX_BYTES)

    result = collector.parse_local_file(
        source, tmp_path / "out", "flat"
    )

    assert result["records_written"] > 0


def test_parse_local_file_writes_dataset(collector, tmp_path):
    source = tmp_path / "flat.xlsx"
    source.write_bytes(FLAT_XLSX_BYTES)

    out = tmp_path / "out"
    out.mkdir()

    result = collector.parse_local_file(source, out, "flat")

    written = json.loads(
        (out / "dataset_flat.json").read_text(encoding="utf-8")
    )

    assert result["records_written"] == len(written)
    assert written[0]["source"] == "nsi"


def test_parse_local_file_reports_count_mismatch(
    collector, tmp_path
):
    """
    Ако unified записите се разминават с парснатите, това
    трябва да е ВИДИМО, а не да се открие по-късно в пайплайна.
    """
    source = tmp_path / "junk.xlsx"
    source.write_bytes(NO_USABLE_ROWS_XLSX_BYTES)

    result = collector.parse_local_file(
        source, tmp_path, "junk"
    )

    assert result["records_parsed"] == 0
    assert result["records_written"] == 0
    assert result["diagnostics"]


def test_fetch_and_parse_rejects_a_path_clearly(
    collector, tmp_path
):
    """
    Преди parse_local_file подаването на Path към
    fetch_and_parse падаше с
    "'WindowsPath' object has no attribute 'decode'" -
    напълно неинформативно. Сега грешката е ясна.
    """
    with pytest.raises(TypeError) as info:
        collector.fetch_and_parse(
            tmp_path / "x.xlsx", tmp_path
        )

    assert "URL" in str(info.value).upper() or "str" in str(
        info.value
    )
