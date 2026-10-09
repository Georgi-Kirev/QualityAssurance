# TAG: TESTS - TEST REPORT GENERATOR
#
# Докладът е това, което човекът вижда, когато нещо се
# счупи. Ако той мълчи, лъже или брои грешно, диагностиката
# е изгубена - а точно тогава е нужна.
#
# Тестовете тук пазят следното:
#
#  1. Броенето е точно. Ръчно пренаписване на "N passed"
#     от изхода на pytest е бъг, който се проявява точно
#     когато нещо се счупи.
#  2. При провал се показва СЪОБЩЕНИЕТО, не само името на
#     теста. "test_x е fail" не помага на никого.
#  3. Името на файла се извежда правилно - и при път извън
#     проекта, където pytest заменя разделителите с точки.
#
# Реалното доказателство за (3) е доказан бъг: при
# classname "Jane Doe.AppData...test_broken" старата
# логика връщаше файл, наречен "Jane Doe.py".

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.report_generator import (
    MAX_SHOWN_FAILURES,
    Report,
    CaseResult,
    build_error_text,
    build_report_text,
    heading,
    human_duration,
    load_report,
    main,
    rule,
)


# ============================================================
# TAG: HELPERS
# ============================================================

def _suite(
    cases: str,
    time: str = "1.0",
) -> ET.Element:
    return ET.fromstring(
        f'<testsuite name="pytest" tests="1" time="{time}">'
        f"{cases}</testsuite>"
    )


def _testcase(
    name: str,
    classname: str,
    time: str = "0.010",
    failure: str = "",
    skipped: bool = False,
) -> ET.Element:

    body = ""

    if failure:
        body = (
            '<failure message="boom: expected 1 got 2" '
            'type="AssertionError">'
            "assert 1 == 2</failure>"
        )
    elif skipped:
        body = '<skipped message="not ready"/>'

    return ET.fromstring(
        f'<testcase name="{name}" classname="{classname}" '
        f'time="{time}">{body}</testcase>'
    )


def _report_from(cases: ET.Element, time: float = 1.0) -> Report:
    return Report(
        [CaseResult(c) for c in cases.iter("testcase")],
        time,
    )


# ============================================================
# TAG: ФОРМАТИРАНЕ
# ============================================================

@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0 ms"),
        (0.004, "4 ms"),
        (0.5, "500 ms"),
        (3.91, "3.91 s"),
        (59.5, "59.50 s"),
        (61.0, "1 min 1.0 s"),
        (3661.0, "1 h 1 min 1 s"),
    ],
)
def test_human_duration_is_readable(seconds, expected):
    """
    "522148.36" и "3.91s" са еднакви информационно. Едното
    е четимо, другото не е.
    """

    assert human_duration(seconds) == expected


def test_rule_width_is_stable():
    assert len(rule()) == len(heading("X").splitlines()[0])


def test_report_text_ends_with_newline():
    """
    Без последен пренос cmd.exe оставя курсора на последния
    ред и изглежда, че прозорецът е увиснал.

    Двойният пренос накрая е ИНТЕНЦИОНАЛЕН - build_footer()
    затваря с празен ред след последната черта.
    """

    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("test_a", "tests.test_x"),
            encoding="unicode",
        )
        + "</suite>"
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 0
    )

    assert text.endswith("\n")
    assert text.rstrip("\n").endswith("=")


# ============================================================
# TAG: ИМЕ НА ФАЙЛ
# ============================================================

@pytest.mark.parametrize(
    "classname,name,expected",
    [
        # Нормалната форма в проекта.
        ("tests.test_matcher", "test_a", "test_matcher.py"),
        # С клас.
        ("tests.test_matcher.TestArea", "test_a", "test_matcher.py"),
        # Път извън проекта. pytest ЗАМЕНЯ разделителите с
        # точки - тук точно се ловеше бъгът.
        (
            "Jane Doe.AppData.Local.Temp.opencode."
            "failing_tests.test_broken",
            "test_a",
            "test_broken.py",
        ),
        # Път със слэшове.
        ("tests/sub/test_deep", "test_a", "test_deep.py"),
        # Форма с :: в името.
        (
            "tests/test_x.py::TestC",
            "test_x.py::TestC::test_a",
            "test_x.py",
        ),
        # Вече с .py.
        ("tests.test_x.py", "test_a", "test_x.py"),
    ],
)
def test_module_name_resolution(
    classname, name, expected
):
    """
    РЕГРЕСИЯ: "Jane Doe.py" като име на тестов файл.
    """

    case = CaseResult(
        ET.fromstring(
            f'<testcase name="{name}" classname="{classname}" '
            f'time="0.1"/>'
        )
    )

    assert case.module == expected


def test_short_name_strips_the_path():
    case = CaseResult(
        ET.fromstring(
            '<testcase name="tests/test_x.py::TestC::test_a" '
            'classname="tests.test_x" time="0.1"/>'
        )
    )

    assert case.short_name == "test_a"


# ============================================================
# TAG: БРОЕНЕ
# ============================================================

def test_counts_by_status():
    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("test_a", "tests.test_x"),
            encoding="unicode",
        )
        + ET.tostring(
            _testcase("test_b", "tests.test_x", failure="x"),
            encoding="unicode",
        )
        + ET.tostring(
            _testcase("test_c", "tests.test_y", skipped=True),
            encoding="unicode",
        )
        + "</suite>"
    )

    report = _report_from(cases)

    assert report.total == 3
    assert report.counts() == {
        "passed": 1,
        "failed": 1,
        "error": 0,
        "skipped": 1,
    }
    assert len(report.problems()) == 1


def test_empty_suite_does_not_divide_by_zero():
    report = Report([], 0.0)

    counts = report.counts()

    assert report.total == 0
    assert counts["passed"] == 0
    assert report.slowest_module() == "?"

    text = build_report_text(report, Path("j.xml"), 0)

    assert "Общо тестове      : 0" in text
    assert "None" not in text.split("ЗАКЛЮЧЕНИЕ")[0]


def test_by_module_groups_and_sorts():
    cases = ET.fromstring(
        "<suite>"
        + "".join(
            ET.tostring(
                _testcase(n, c), encoding="unicode"
            )
            for n, c in (
                ("t1", "tests.test_b"),
                ("t2", "tests.test_a"),
                ("t3", "tests.test_b"),
            )
        )
        + "</suite>"
    )

    rows = _report_from(cases).by_module()

    assert [r[0] for r in rows] == ["test_b.py", "test_a.py"]
    assert rows[0][1] == 2


def test_slowest_module_is_reported():
    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("t1", "tests.test_fast", time="0.01"),
            encoding="unicode",
        )
        + ET.tostring(
            _testcase("t2", "tests.test_slow", time="5.00"),
            encoding="unicode",
        )
        + "</suite>"
    )

    assert _report_from(cases).slowest_module() == "test_slow.py"


# ============================================================
# TAG: ДОКЛАДЪТ ПРИ УСПЕХ
# ============================================================

def test_success_report_states_the_numbers():
    cases = ET.fromstring(
        "<suite>"
        + "".join(
            ET.tostring(
                _testcase(f"t{i}", "tests.test_x"),
                encoding="unicode",
            )
            for i in range(7)
        )
        + "</suite>"
    )

    text = build_report_text(
        _report_from(cases, 2.5), Path("j.xml"), 0
    )

    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ" in text
    assert "Общо тестове      : 7" in text
    assert "Успешни           : 7" in text
    assert "2.50 s" in text
    assert "ИЗХОДЕН КОД" not in text
    assert "Изходен код       : 0" in text


def test_success_report_does_not_list_failures():
    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("t1", "tests.test_x"), encoding="unicode"
        )
        + "</suite>"
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 0
    )

    assert "ГРЕШКИ" not in text
    assert "НЕУСПЕШНИ" not in text


def test_success_report_is_honest_about_scope():
    """
    592 успешни теста не означават, че продуктът е пълен.
    Докладът не бива да внушава обратното.
    """

    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("t1", "tests.test_x"), encoding="unicode"
        )
        + "</suite>"
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 0
    )

    assert "не значи, че продуктът е" in text
    assert "ограничения" in text


# ============================================================
# TAG: ДОКЛАДЪТ ПРИ ПРОВАЛ
# ============================================================

def test_failure_report_shows_the_message():
    """
    Това е смисълът на целия файл. "test_x е fail" не
    помага. Съобщението на pytest - помага.
    """

    cases = _suite(
        ET.tostring(
            _testcase(
                "test_raises", "tests.test_x", failure="x"
            ),
            encoding="unicode",
        )
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 1
    )

    assert "НЕУСПЕШНИ         : 1" in text
    assert "ИМА 1 ПРОБЛЕМА" in text
    assert "boom: expected 1 got 2" in text
    assert "assert 1 == 2" in text
    assert "AssertionError" in text
    assert "test_raises" in text


def test_failure_report_shows_the_file():
    cases = _suite(
        ET.tostring(
            _testcase(
                "test_raises", "tests.test_broken", failure="x"
            ),
            encoding="unicode",
        )
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 1
    )

    assert "test_broken.py::test_raises" in text


def test_error_is_distinguished_from_failure():
    xml = (
        '<testsuite name="pytest" time="0.1">'
        '<testcase name="test_x" classname="tests.test_x" '
        'time="0.1"><error message="ImportError" '
        'type="ImportError">cannot import name X'
        "</error></testcase></testsuite>"
    )

    report = _report_from(ET.fromstring(xml))

    assert report.counts()["error"] == 1

    text = build_report_text(
        report, Path("j.xml"), 2
    )

    assert "ГРЕШКИ при изпълн.: 1" in text
    assert "ГРЕШКА" in text
    assert "cannot import name X" in text


def test_failed_but_zero_exit_code_is_still_reported():
    """
    pytest може да върне 0 при xfail-подобни ситуации.
    Заключението гледа И двата сигнала, не само exit кода.
    """

    cases = _suite(
        ET.tostring(
            _testcase("t", "tests.test_x", failure="x"),
            encoding="unicode",
        )
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 0
    )

    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ" not in text
    assert "ИМА 1 ПРОБЛЕМА" in text


def test_skipped_are_counted_separately():
    """
    Пропуснатите тестове не са успешни. Смесването им
    прави броя наивно да изглежда по-добър, отколкото е.
    """

    cases = ET.fromstring(
        "<suite>"
        + ET.tostring(
            _testcase("t1", "tests.test_x"),
            encoding="unicode",
        )
        + ET.tostring(
            _testcase("t2", "tests.test_x", skipped=True),
            encoding="unicode",
        )
        + "</suite>"
    )

    text = build_report_text(
        _report_from(cases), Path("j.xml"), 0
    )

    assert "Пропуснати        : 1" in text
    assert "Успешни           : 1" in text


def test_too_many_failures_are_counted_not_dumped():
    """
    300 провала трябва да дадат 27 реда, не 300 екрана.
    Но БРОЯТ трябва да е верен - иначе човекът решава, че
    има само 25 проблема.
    """

    cases = _suite(
        "".join(
            ET.tostring(
                _testcase(
                    f"t{i}", "tests.test_x", failure="x"
                ),
                encoding="unicode",
            )
            for i in range(MAX_SHOWN_FAILURES + 7)
        )
    )

    report = _report_from(cases)

    text = build_report_text(
        report, Path("j.xml"), 1
    )

    assert len(report.problems()) == MAX_SHOWN_FAILURES + 7
    assert (
        f"още {7} неуспешни теста" in text
    )
    assert text.count("ПРОВАЛ") == MAX_SHOWN_FAILURES


# ============================================================
# TAG: ЗАГРУЖДАНЕ
# ============================================================

def test_loads_real_pytest_shape(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests">'
        '<testsuite name="pytest" tests="2" failures="1" '
        'time="0.5">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.1"/>'
        '<testcase name="test_b" classname="tests.test_x" '
        'time="0.4"><failure message="nope" type="Assert">'
        "assert 0</failure></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    report = load_report(path)

    assert report.total == 2
    assert report.total_time == 0.5
    assert report.counts()["failed"] == 1


def test_loads_bare_testsuite(tmp_path):
    """
    Някои инструменти пишат <testsuite> без обвивката.
    Форматът е валиден и ние сме длъжни да го четем.
    """

    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuite name="pytest" time="0.25">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.25"/></testsuite>',
        encoding="utf-8",
    )

    assert load_report(path).total_time == 0.25


def test_missing_xml_explains_instead_of_crashing(tmp_path):
    """
    Липсващ XML е реален случай: pytest е гръмнал преди да
    запише. Съобщението трябва да казва какво да се гледа.
    """

    with pytest.raises(SystemExit) as info:
        load_report(tmp_path / "nope.xml")

    message = str(info.value)

    assert "not found" in message
    assert "collection error" in message


def test_malformed_xml_is_reported(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text("<testsuite>", encoding="utf-8")

    with pytest.raises(SystemExit) as info:
        load_report(path)

    assert "malformed" in str(info.value)


# ============================================================
# TAG: ENTRY POINT
# ============================================================

def test_main_prints_and_saves(tmp_path, capsys):
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite name="pytest" time="0.5">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.5"/></testsuite>',
        encoding="utf-8",
    )

    saved = tmp_path / "out" / "report.txt"

    code = main(
        [
            "report_generator.py",
            str(xml),
            "0",
            "--save",
            str(saved),
        ]
    )

    assert code == 0
    assert saved.exists()

    on_disk = saved.read_text(encoding="utf-8")
    printed = capsys.readouterr().out

    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ" in on_disk
    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ" in printed
    # Конзолата получава CRLF, файлът - LF на стандартното.
    assert "\r\n" in printed


def test_main_without_arguments_explains(capsys):
    assert main(["report_generator.py"]) == 2
    assert "usage" in capsys.readouterr().out


def test_main_survives_missing_xml(tmp_path, capsys):
    code = main(
        [
            "report_generator.py",
            str(tmp_path / "nope.xml"),
            "0",
        ]
    )

    assert code == 3
    assert "not found" in capsys.readouterr().out


def test_error_text_is_visible():
    text = build_error_text("something broke")

    assert "!!!" in text
    assert "something broke" in text


# ============================================================
# TAG: КОДИРАНЕ
#
# Измерено на тази машина на 30.09.2026:
#
#   chcp                -> 866   (OEM Cyrillic)
#   sys.stdout.encoding -> cp1251 (ANSI Cyrillic, локал)
#
# Те са РАЗЛИЧНИ. Писане на cp1251 в CP 866 конзола дава
# mojibake. И ако просто наложим UTF-8, чуваме CP 866.
#
# Затова изходът се кодира според ИЗТОЧНИКА:
#   * истинска конзола -> нейната кодировка (дефинитивно
#     правилна, Python я взима от GetConsoleOutputCP)
#   * файл / тръба     -> UTF-8 (chcp не действа там, а
#     локалната кодировка е cp1251 и е произволна)
# ============================================================

def test_console_output_is_utf8_when_redirected(
    tmp_path, capsysbinary
):
    """
    Пренасочен изход = UTF-8, без значение каква е локалната
    кодировка на машината.
    """

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite name="pytest" time="0.5">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.5"/></testsuite>',
        encoding="utf-8",
    )

    main(
        [
            "report_generator.py",
            str(xml),
            "0",
        ]
    )

    raw = capsysbinary.readouterr().out

    assert b"\xff\xfd" not in raw
    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ".encode(
        "utf-8"
    ) in raw


def test_saved_report_is_always_utf8(tmp_path):
    """
    Файлът не зависи от конзолата изобщо.
    """

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite name="pytest" time="0.5">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.5"/></testsuite>',
        encoding="utf-8",
    )

    saved = tmp_path / "report.txt"

    main(
        [
            "report_generator.py",
            str(xml),
            "0",
            "--save",
            str(saved),
        ]
    )

    text = saved.read_text(encoding="utf-8")

    assert "ВСИЧКИ ТЕСТОВЕ УСПЕШНИ" in text
    assert "ТЕСТОВ ДОКЛАД" in text


def test_console_output_uses_crlf(tmp_path, capsysbinary):
    """
    Без CRLF cmd.exe оставя курсора на последния ред и
    прозорецът изглежда увиснал.
    """

    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuite name="pytest" time="0.5">'
        '<testcase name="test_a" classname="tests.test_x" '
        'time="0.5"/></testsuite>',
        encoding="utf-8",
    )

    main(["report_generator.py", str(xml), "0"])

    raw = capsysbinary.readouterr().out

    assert b"\r\n" in raw
    assert raw.endswith(b"\r\n")
