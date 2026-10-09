# TAG: TEST REPORT GENERATOR
#
# Чете JUnit XML, който pytest пише, и печата доклад за
# човек - не за машина.
#
# ЗАЩО XML, А НЕ ПАРСЕНЕ НА ИЗХОДА
#
# Изходът на pytest е човешки текст и се променя между
# версии. JUnit XML е стабилен формат, идва от вграден
# плъгин (без нова зависимост) и носи ВСИЧКО - име на
# теста, клас, файл, ВРЕМЕ, текст на грешката, traceback.
# Опитът да се изскрейт текста ("N passed, M failed") е
# тъкмо защото докладът после или се чупи, или тихо
# показва грешно число.
#
# Какво дава докладът:
#   * брой тестове по статус - без да се брои на пръсти
#   * реално отнето време + най-бавните тестове
#   * разбивка по тестов файл
#   * при провал: пълното съобщение и traceback-ът, а не
#     само името на теста
#
# Не изпълнява тестове. Само чете, изгражда и отпечатва.
#
# ИЗПОЛЗВАНЕ:
#     python tests/report_generator.py <junit.xml> [exit_code]
#         [--save <path.txt>]

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WIDTH = 78

# Колко провала се показват подробно. Повече е шум и
# диагностиката става нечетима. Останалите се броят.
MAX_SHOWN_FAILURES = 25

MAX_SLOW_TESTS = 8

# Колко символа от съобщението/traceback-а да се покажат.
# Пълният traceback на pytest може да е 200 реда, а
# причината обикновено е в първите няколко.
MAX_TRACEBACK_CHARS = 1800


# ============================================================
# TAG: FORMATTING
# ============================================================

def rule(char: str = "=") -> str:
    return char * WIDTH


def heading(title: str) -> str:
    text = f" {title} "
    pad = max(0, WIDTH - len(text))
    left = pad // 2
    return (
        rule()
        + "\n"
        + " " * left
        + text
        + "\n"
        + rule()
    )


def human_duration(seconds: float) -> str:
    """
    "3.91s" е безполезно, когато suite-ът порасне.
    """

    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"

    if seconds < 60:
        return f"{seconds:.2f} s"

    minutes, rest = divmod(seconds, 60)

    if minutes < 60:
        return f"{int(minutes)} min {rest:.1f} s"

    hours, minutes = divmod(int(minutes), 60)

    return f"{hours} h {minutes} min {rest:.0f} s"


class Buffer:
    """
    Натрупва редове, после ги отдава наведнъж.
    """

    def __init__(self) -> None:
        self.lines: List[str] = []

    def line(self, text: str = "") -> "Buffer":
        self.lines.append(text)
        return self

    def many(self, text: str) -> "Buffer":
        for item in text.splitlines():
            self.lines.append(item)
        return self

    def blank(self, count: int = 1) -> "Buffer":
        for _ in range(count):
            self.lines.append("")
        return self

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# ============================================================
# TAG: XML -> STRUCTURED DATA
# ============================================================

def _as_float(value: Optional[str]) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class CaseResult:
    """
    Един testcase от JUnit XML.

    Кръстено на CaseResult, а не на TestCase, НАРОЧНО:
    името Test* кара pytest да се опитва да го събере
    като тестов клас (PytestCollectionWarning) всеки път,
    когато някой го импортира в тестов файл. Това е
    предупреждение, което никой не чете, но замърсява
    изхода на всеки рън.
    """

    def __init__(self, element: ET.Element):
        self.name: str = (
            element.get("name") or element.get("classname") or "?"
        )
        self.classname: str = element.get("classname") or ""
        self.file: str = element.get("file") or ""
        self.line: str = element.get("line") or ""
        self.time: float = _as_float(element.get("time"))

        self.status = "passed"
        self.message = ""
        self.traceback = ""
        self.kind = ""

        self._read_outcome(element)

    def _read_outcome(self, element: ET.Element) -> None:
        failure = element.find("failure")
        error = element.find("error")
        skipped = element.find("skipped")

        if failure is not None:
            self.status = "failed"
            self.message = failure.get("message") or ""
            self.traceback = failure.text or ""
            self.kind = failure.get("type") or ""
            return

        if error is not None:
            self.status = "error"
            self.message = error.get("message") or ""
            self.traceback = error.text or ""
            self.kind = error.get("type") or ""
            return

        if skipped is not None:
            self.status = "skipped"
            self.message = skipped.get("message") or ""

        # xfail / xpass не се маркират в junitxml като
        # failure/skipped - те се записват като минал тест.
        # Тук ги броим като passed, което е приемливо: те
        # не са build breakage и не бива да вдигат ръка.

    @property
    def module(self) -> str:
        """
        Името на тестовия файл, изведено от classname.

        Тук се трябва малко внимание, защото pytest НЕ пише
        пътя до файла в junitxml. Наблюдавани форми на
        classname при pytest 9:

            tests.test_matcher
                модул, тестовете са в клас

            tests.test_matcher.TestArea
                модул + клас

            Jane Doe.AppData.Local.Temp...test_broken
                път извън проекта; pytest ЗАМЕНЯ разделителите
                с точки, така че "C:\\Users\\..." става
                "C.Users..."

        Опитът "вземи последната точка" дава "Kirev.py" за
        втория пример, а "вземи името на файла" изобщо не
        работи, защото "/" вече го няма.

        Надеждният котка: последният сегмент, който
        започва с "test_". Това различава ФАЙЛ от КЛАС -
        pytest именува файловете test_*.py, а класовете
        Test* (без долна черта). "TestArea" не бива да
        спечели пред "test_matcher".

        Ако няма такъв сегмент, падаме на последния - и
        това е по-лошо, но поне не измисля име.
        """

        raw = self.classname or self.name

        # Форма с път: "tests/sub/test_x.py::TestY"
        if "::" in raw:
            head = raw.split("::")[0]

            if head.endswith(".py"):
                return Path(
                    head.replace("\\", "/")
                ).name

            raw = head

        # Нормализираме разделителите, ако има.
        if "/" in raw or "\\" in raw:

            candidate = Path(
                raw.replace("\\", "/")
            ).name

            if candidate.endswith(".py"):
                return candidate

            raw = candidate

        parts = [p for p in raw.split(".") if p]

        if not parts:
            return "?"

        for part in reversed(parts):

            lowered = part.lower()

            if lowered.startswith("test_") or (
                lowered in {"test", "tests"}
            ):
                return part + ".py"

        last = parts[-1]

        if last.endswith(".py"):
            return last

        return last + ".py"


    @property
    def short_name(self) -> str:
        raw = self.name

        if "::" in raw:
            return raw.split("::")[-1]

        return raw


class Report:
    def __init__(self, cases: List[CaseResult], total_time: float):
        self.cases = cases
        self.total_time = total_time

    def counts(self) -> Dict[str, int]:
        result = {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "skipped": 0,
        }

        for case in self.cases:
            result[case.status] = (
                result.get(case.status, 0) + 1
            )

        return result

    @property
    def total(self) -> int:
        return len(self.cases)

    def problems(self) -> List[CaseResult]:
        return [
            case for case in self.cases
            if case.status in {"failed", "error"}
        ]

    def slowest(self) -> List[CaseResult]:
        return sorted(
            self.cases,
            key=lambda c: c.time,
            reverse=True,
        )[:MAX_SLOW_TESTS]

    def by_module(
        self,
    ) -> List[Tuple[str, int, float, int]]:

        buckets: Dict[str, List[CaseResult]] = defaultdict(list)

        for case in self.cases:
            buckets[case.module].append(case)

        rows = []

        for module, items in buckets.items():

            rows.append((
                module,
                len(items),
                sum(i.time for i in items),
                sum(
                    1 for i in items
                    if i.status in {"failed", "error"}
                ),
            ))

        return sorted(rows, key=lambda r: (-r[1], r[0]))

    def slowest_module(self) -> str:
        rows = self.by_module()

        if not rows:
            return "?"

        return max(rows, key=lambda r: r[2])[0]


# ============================================================
# TAG: LOADING
# ============================================================

def load_report(xml_path: Path) -> Report:
    if not xml_path.exists():
        raise SystemExit(
            f"JUnit XML not found: {xml_path}\n"
            "pytest did not produce a report. Check the "
            "output above for an early crash (bad args, "
            "collection error, missing dependency)."
        )

    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as error:
        raise SystemExit(
            f"JUnit XML is malformed: {xml_path}\n{error}"
        )

    # pytest пише <testsuites><testsuite>..., но други
    # инструменти дават директно <testsuite>. Двете се
    # поддържат.
    suites = (
        root.findall("testsuite")
        if root.tag == "testsuites"
        else [root]
    )

    cases: List[CaseResult] = []

    for suite in suites:
        for element in suite.iter("testcase"):
            cases.append(CaseResult(element))

    total_time = max(
        [_as_float(s.get("time")) for s in suites] or [0.0]
    )

    if not total_time:
        total_time = sum(case.time for case in cases)

    return Report(cases, total_time)


# ============================================================
# TAG: BUILDING THE REPORT
#
# Докладът се ИЗГРАЖДА веднъж в текст, после се отпечатва
# на конзолата и се записва във файл. Причината е чисто
# техническа и се прояви на Windows:
#
#   * прекият изход на Python към конзола минава през
#     Unicode API и кирилицата е наред;
#   * пренасочването към файл дава UTF-8 байтове, които
#     cmd.exe показва с активната кодова страница и
#     получаваш mojibake.
#
# Изграждането веднъж решава и двете: файлът пише UTF-8
# явно, конзолата получава текста през Unicode пътя.
# ============================================================

def build_header(
    buffer: Buffer,
    xml_path: Path,
    exit_code: int,
) -> None:

    buffer.blank()
    buffer.line(heading("ТЕСТОВ ДОКЛАД"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    buffer.line(f"  Стартиран на      : {now}")
    buffer.line(f"  Python            : {sys.version.split()[0]}")
    buffer.line(f"  Изходен код       : {exit_code}")
    buffer.line(f"  JUnit XML         : {xml_path}")
    buffer.blank()


def build_summary(
    buffer: Buffer,
    report: Report,
) -> None:

    counts = report.counts()
    total = report.total

    buffer.line(heading("ОБЩО"))

    buffer.line(f"  Общо тестове      : {total:,}")
    buffer.line(f"  Успешни           : {counts['passed']:,}")

    if counts["skipped"]:
        buffer.line(
            f"  Пропуснати        : {counts['skipped']:,}"
        )

    if counts["failed"]:
        buffer.line(
            f"  НЕУСПЕШНИ         : {counts['failed']:,}"
        )

    if counts["error"]:
        buffer.line(
            f"  ГРЕШКИ при изпълн.: {counts['error']:,}"
        )

    buffer.blank()
    buffer.line(
        "  Отнето време      : "
        f"{human_duration(report.total_time)}"
    )

    if total:
        buffer.line(
            "  Средно на тест    : "
            f"{human_duration(report.total_time / total)}"
        )

    buffer.line(
        f"  Най-бавен файл    : {report.slowest_module()}"
    )
    buffer.blank()


def build_verdict(
    buffer: Buffer,
    report: Report,
    exit_code: int,
) -> None:

    problems = report.problems()
    counts = report.counts()

    buffer.line(heading("ЗАКЛЮЧЕНИЕ"))

    if exit_code == 0 and not problems:

        buffer.blank()
        buffer.line("  РЕЗУЛТАТ: ВСИЧКИ ТЕСТОВЕ УСПЕШНИ")
        buffer.blank()
        buffer.line(
            f"  {counts['passed']:,} теста преминаха за "
            f"{human_duration(report.total_time)}."
        )
        buffer.blank()
        buffer.line(
            "  Пайплайнът е в съгласувано състояние спрямо"
        )
        buffer.line(
            "  тестовете. Това не значи, че продуктът е"
        )
        buffer.line(
            "  пълен - виж SESSION_*.md и README.md за"
        )
        buffer.line(
            "  известните ограничения на данните."
        )
        buffer.blank()

        return

    suffix = "А" if len(problems) == 1 else "И"

    buffer.blank()
    buffer.line(
        f"  РЕЗУЛТАТ: ИМА {len(problems)} ПРОБЛЕМ{suffix}"
    )
    buffer.blank()
    buffer.line(
        "  Списъкът по-долу е точно за диагностика."
    )
    buffer.line(
        "  Всяко съобщение е текстът, който pytest е"
    )
    buffer.line("  отпечатал - не преразказ.")
    buffer.blank()


def build_failures(
    buffer: Buffer,
    report: Report,
) -> None:

    problems = report.problems()

    if not problems:
        return

    buffer.line(heading("ГРЕШКИ"))

    shown = problems[:MAX_SHOWN_FAILURES]
    hidden = len(problems) - len(shown)

    for index, case in enumerate(shown, start=1):

        marker = (
            "ГРЕШКА" if case.status == "error" else "ПРОВАЛ"
        )

        buffer.blank()
        buffer.line(
            f"  [{index}] {marker}  "
            f"{case.module}::{case.short_name}"
        )
        buffer.line(f"      classname : {case.classname}")
        buffer.line(f"      име       : {case.name}")
        buffer.line(
            f"      време     : {human_duration(case.time)}"
        )

        if case.kind:
            buffer.line(f"      тип       : {case.kind}")

        if case.message:

            text = case.message.strip()

            if len(text) > MAX_TRACEBACK_CHARS:
                text = text[:MAX_TRACEBACK_CHARS] + " ..."

            buffer.blank()
            buffer.line("      съобщение :")
            buffer.many(_indent(text))

        if case.traceback:

            text = case.traceback.strip()

            if len(text) > MAX_TRACEBACK_CHARS:
                text = (
                    text[:MAX_TRACEBACK_CHARS]
                    + "\n... (пълното е в извода на pytest)"
                )

            buffer.blank()
            buffer.line("      traceback :")
            buffer.many(_indent(text))

    if hidden > 0:

        buffer.blank()
        buffer.line(
            f"  ... и още {hidden} неуспешни теста "
            f"(не са показани)."
        )
        buffer.line(
            "  Пълният списък е в извода на pytest по-горе"
        )
        buffer.line("  и в JUnit XML файла.")

    buffer.blank()


def _indent(text: str, prefix: str = "        ") -> str:
    return "\n".join(
        f"{prefix}{line}" for line in text.splitlines()
    )


def build_modules(
    buffer: Buffer,
    report: Report,
) -> None:

    rows = report.by_module()

    if not rows:
        return

    buffer.line(heading("ПО ФАЙЛ"))

    name_width = min(
        max(len(row[0]) for row in rows),
        44,
    )

    buffer.blank()
    buffer.line(
        f"  {'Файл':<{name_width}}  "
        f"{'тестове':>8s}  "
        f"{'време':>10s}  "
        f"{'проблеми':>8s}"
    )
    buffer.line("  " + "-" * (name_width + 34))

    for module, count, seconds, bad in rows:

        name = module

        if len(name) > name_width:
            name = name[: name_width - 1] + "~"

        flag = f"{bad}" if bad else "-"

        buffer.line(
            f"  {name:<{name_width}}  "
            f"{count:>8,}  "
            f"{human_duration(seconds):>10s}  "
            f"{flag:>8s}"
        )

    buffer.blank()


def build_slowest(
    buffer: Buffer,
    report: Report,
) -> None:

    rows = report.slowest()

    if not rows:
        return

    if (
        report.total_time
        and rows[0].time / report.total_time > 0.5
    ):

        buffer.line(heading("НАЙ-БАВЕН ТЕСТ"))

        top = rows[0]
        share = top.time / report.total_time * 100

        buffer.blank()
        buffer.line(f"  {top.module}::{top.short_name}")
        buffer.line(
            f"  {human_duration(top.time)} "
            f"({share:.1f}% от цялото време)"
        )
        buffer.blank()

    buffer.line(heading("НАЙ-БАВНИ ТЕСТОВЕ"))

    buffer.blank()
    buffer.line(f"  {'време':>10s}  {'статус':<8s}  тест")
    buffer.line("  " + "-" * (WIDTH - 4))

    for case in rows:

        buffer.line(
            f"  {human_duration(case.time):>10s}  "
            f"{case.status:<8s}  "
            f"{case.module}::{case.short_name}"
        )

    buffer.blank()


def build_footer(
    buffer: Buffer,
    xml_path: Path,
) -> None:

    buffer.line(rule())
    buffer.blank()
    buffer.line(
        "  Пълните данни, в machine-readable формат, са в:"
    )
    buffer.line(f"    {xml_path}")
    buffer.blank()
    buffer.line("  За да видиш отново само провалите:")
    buffer.line(
        "    .venv\\Scripts\\python.exe "
        "-m pytest tests/ --lf -v"
    )
    buffer.blank()
    buffer.line(rule())
    buffer.blank()


def build_report_text(
    report: Report,
    xml_path: Path,
    exit_code: int,
) -> str:

    buffer = Buffer()

    build_header(buffer, xml_path, exit_code)
    build_summary(buffer, report)
    build_verdict(buffer, report, exit_code)
    build_failures(buffer, report)
    build_modules(buffer, report)
    build_slowest(buffer, report)
    build_footer(buffer, xml_path)

    return buffer.text()


def build_error_text(message: str) -> str:

    buffer = Buffer()

    buffer.blank()
    buffer.line(rule("!"))
    buffer.blank()
    buffer.line(f"  {message}")
    buffer.blank()
    buffer.line(rule("!"))
    buffer.blank()

    return buffer.text()


# ============================================================
# TAG: ENTRY POINT
# ============================================================

def _write_console(text: str) -> None:
    """
    Напиши текста на конзолата, кодиран ТОЧНО както тя
    очаква.

    Защо не се вика chcp 65001 от run_tests.bat, защото
    изходът трябва да е наред при всяка настройка на
    потребителя, без да му се пипа конзолата:

      * cmd.exe чете .bat файл байт по байт. Смяна на
        кодова страница ПОСРЕД изпълнението кара cmd.exe
        да претълкува останалите байтове с новата страница
        и скриптът се разпада. Измерено на 30.09.2026.

      * Python знае каква е кодировката на ИСТИНСКАТА
        конзола (sys.stdout.encoding). Ако текстът влезе в
        нея, Windows го рисува правилно.

    ДВА СЛУЧАЯ, защото източникът на изхода е различен:

      * Истинска конзола (двоен клик). Python взима
        кодировката от GetConsoleOutputCP(), затова
        кодираме с нея - тя е definitionally правилната.

      * Пренасочен файл или тръба. Python тогава използва
        ЛОКАЛНАТА кодировка, не конзолната, и chcp 65001
        няма ефект. При измерено на тази машина локалната
        е cp1251, а конзолата е CP 866 - две различни
        неща. За файл пишем UTF-8, което е еднозначно
        правилно и за редактор, и за diff, и за машина.
    """

    # CRLF: cmd.exe без тях оставя курсора на последния
    # ред и изглежда, че прозорецът е увиснал.
    payload = text.replace("\n", "\r\n")

    try:
        attached_to_console = sys.stdout.isatty()
    except (AttributeError, ValueError):
        attached_to_console = False

    if attached_to_console:
        encoding = (
            getattr(sys.stdout, "encoding", None)
            or "utf-8"
        )
    else:
        encoding = "utf-8"

    try:
        data = payload.encode(
            encoding, errors="replace"
        )
    except (LookupError, UnicodeEncodeError):
        data = payload.encode(
            "utf-8", errors="replace"
        )

    buffer = getattr(sys.stdout, "buffer", None)

    if buffer is not None:

        buffer.write(data)
        buffer.flush()
        return

    sys.stdout.write(payload)
    sys.stdout.flush()



def main(argv: List[str]) -> int:

    args = [

        a for a in argv[1:] if not a.startswith("--")
    ]

    save_path: Optional[str] = None

    for index, item in enumerate(argv[1:]):

        if item == "--save" and index + 2 <= len(argv) - 1:
            save_path = argv[index + 2]

    if not args:

        _write_console(
            build_error_text(
                "usage: report_generator.py <junit.xml> "
                "[exit_code] [--save <path.txt>]"
            )
        )

        return 2

    xml_path = Path(args[0]).resolve()

    try:
        exit_code = int(args[1]) if len(args) > 1 else 0
    except ValueError:
        exit_code = 0

    try:
        report = load_report(xml_path)
        text = build_report_text(
            report, xml_path, exit_code
        )
    except SystemExit as error:
        text = build_error_text(str(error))
        _save(text, save_path)
        _write_console(text)
        return 3

    _save(text, save_path)
    _write_console(text)

    return 0


def _save(text: str, save_path: Optional[str]) -> None:

    if not save_path:
        return

    path = Path(save_path)

    try:
        path.parent.mkdir(
            parents=True, exist_ok=True
        )
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        _write_console(
            build_error_text(
                f"Could not save report to {path}: {error}"
            )
        )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
