# TAG: NSI COLLECTOR
# Collector за Националния статистически институт.
#
# ИЗТОЧНИК:
#   https://www.nsi.bg
#   НСИ - държавен орган, официална статистика
#
# ЗАЩО ТОЗИ ИЗТОЧНИК Е ВАЖЕН:
#   SofiaPlan дава ЦЕНА НА ЗОНА (и то само за 1 dataset).
#   НСИ дава ИНДЕКС НА ЦЕНИТЕ НА ЖИЛИЩА (HPI), изчислен от
#   НОТАРИАЛНИ СДЕЛКИ - тоест реални пазарни цени, а не
#   обяви и не оценки. От 2022 г. включва апартаменти и къщи.
#
#   Приходът, използван от НСИ, е ПЪЛНА цена на сделката и
#   включва стойността на земята. Индексът е chain-linked
#   Laspeyres тип, което означава, че отчита промяната на
#   качеството, а не само инфлацията.
#
# ПРОВЕРЕНО НА 28.09.2026:
#   GET https://www.nsi.bg/en/statistical-data/99  -> HTTP 200
#       (House price indices, national level)
#   GET https://www.nsi.bg/robots.txt             -> 200
#       "User-agent: * / Disallow: /admin/"
#   Т.е. публичното съдържание е РАЗРЕШЕНО за автоматично
#   четене. Забранен е само /admin/.
#
# ФОРМАТ - ВАЖНО:
#   НСИ НЕ ПРЕДОСТАВЯ REST API за тези данни. Публикува
#   XLSX и .regnp файлове на директни адреси. Затова:
#     * каталогът на показателите се чете от HTML;
#     * самата числова таблица изисква ЗНАЙ ТОЧНИЯ URL на
#       файла за конкретния отчет.
#
#   Тук НЕ Е ИЗМИСЛЕН URL. Вместо това даваме:
#     1. discover_indicators() - намира реалните показатели;
#     2. fetch_indicator_file() - сваля файл ПО ЯВЕН адрес,
#        подаден от потребителя, след проверка на домейна.
#
#   Това е по-честно и по-безопасно от твърдение, че нещо
#   работи, без да е проверено.
#
# ПРАВНА СТРАНИЦА:
#   Официална държавна статистика, публикувана за свободен
#   достъп, robots.txt изрично допуска публичното съдържание.
#   Условията за ползване на НСИ изискват посочване на
#   източника при цитиране - което collect() записва в
#   metadata.

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from netpolicy import require_network

from .base import BaseCollector


TIMEZONE = ZoneInfo("Europe/Sofia")


class NsiCollector(BaseCollector):
    # TAG: SOURCE NAME
    SOURCE_NAME = "nsi"

    # TAG: ENDPOINTS (проверени на 28.09.2026)
    BASE_URL = "https://www.nsi.bg"

    # --------------------------------------------------------
    # TAG: РЕАЛНИТЕ АДРЕСИ - ОТКРИТИ ЕМПИРИЧНО
    #
    # Първият опит гледаше /statistical-data/90 и /99 и
    # върна 0 файла. Причината, установена с 5 заявки:
    #
    #   /statistical-data/90..99  са МЕТАДАННИ страници -
    #   обяснения, правна основа, контакти. Нямат файлове.
    #
    #   Реалните числа са на /en/statistical-data/<id>/<subid>
    #   и връзка към XLSX в акордеон "Time series".
    #
    #   Проверени работни адреси:
    #     /en/statistical-data/98/331 -> HPI_2.1-en.xlsx
    #        (HPI по статистически региони, 2015Q1..2026Q2)
    #
    # Забележка за URL: връзката в HTML е ОТНОСИТЕЛНА
    # ("sites/default/files/...") и НЕ се раз resolв-ва
    # спрямо текущата страница - тя лежи в корена на
    # домейна. Първият опит да я свали относително върна 404.
    # --------------------------------------------------------
    HOUSE_PRICE_PAGE = "/en/statistical-data/98/331"

    BULGARIAN_PRICE_PAGE = "/en/statistical-data/98/331"

    # Адреси, които вече са проверени и НЕ водят до файл.
    # Записани, за да не се повтарят безсмислени обиколки.
    METADATA_ONLY_PAGES = (
        "/statistical-data/90",
        "/en/statistical-data/99",
        "/statistical-data/96",
        "/en/statistical-data/96",
    )

    # TAG: POLITE CLIENT
    USER_AGENT = (
        "AIPropertyMarket/1.0 "
        "(official statistics collector; +local research use)"
    )
    REQUEST_TIMEOUT = 60
    MIN_INTERVAL_SECONDS = 2.0

    _last_request = [0.0]

    # TAG: ALLOWED HOSTS
    #
    # Защита срещу случайно или злонамерено подаване на чужд
    # адрес. Collector-ът може да сваля САМО от официалния
    # домейн на НСИ.
    ALLOWED_HOSTS = {"www.nsi.bg", "nsi.bg"}

    def __init__(self, raw_dir: Path):
        super().__init__(self.SOURCE_NAME, raw_dir)

    # TAG: RATE LIMIT
    def _wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request[0]
        if elapsed < self.MIN_INTERVAL_SECONDS:
            time.sleep(self.MIN_INTERVAL_SECONDS - elapsed)
        self._last_request[0] = time.monotonic()

    # TAG: HTTP
    def _get(self, url: str) -> bytes:
        require_network(f"NSI statistics: {url}")

        self._wait()
        request = Request(
            url,
            headers={"User-Agent": self.USER_AGENT},
        )
        with urlopen(request, timeout=self.REQUEST_TIMEOUT) as response:
            return response.read()

    # TAG: DOMAIN GUARD
    def _assert_allowed(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if host not in self.ALLOWED_HOSTS:
            raise ValueError(
                f"Refusing to fetch from {host!r}. "
                f"Allowed hosts: {sorted(self.ALLOWED_HOSTS)}"
            )

    # TAG: INDICATOR DISCOVERY
    #
    # ПРОВЕРЕНО НА ЖИВО 28.09.2026, ЕМПИРИЧНО:
    #
    #   /statistical-data/90       -> HTTP 200, само текст
    #   /statistical-data/96       -> HTTP 200, само текст
    #   /en/statistical-data/99    -> HTTP 200, само текст
    #   /en/statistical-data/98/331-> HTTP 200 + XLSX връзка
    #
    #   Първите три са МЕТАДАННИ страници (правна основа,
    #   контакти, дати на публикуване). Четвъртата съдържа
    #   акордеон "Time series" с реални числа.
    #
    # ИЗВОДЪТ, КОЙТО ПРЕДИ СЕ ГРЕШЕШЕ ПРИЕМАШЕ ЗА
    # САМОТЕОРИЯ: файловете СА откриваеми. Грешката беше
    # в избора на страницата, не в метода.
    #
    # Методът днес:
    #   1. чете страницата;
    #   2. вади файлните връзки, разрешавайки ги СРЕЩУ
    #      КОРЕНА на домейна (виж TAG: FILE LINKS);
    #   3. при липса връща ЯВНА диагностика, вместо да
    #      мълчи с празен списък.

    DATA_FILE_PATTERN = re.compile(
        r'href="([^"]+\.(?:xlsx?|xlsm|zip|csv|regnp))"',
        re.IGNORECASE,
    )

    ANY_HREF_PATTERN = re.compile(
        r'href="([^"]+)"',
        re.IGNORECASE,
    )

    def _parse_data_file_links(
        self, html: str
    ) -> List[Dict[str, str]]:
        """
        Извлича файлните връзки и ги прави АБСОЛЮТНИ.

        # TAG: FILE LINKS (РЕГРЕСИЯ, открита на живо)
        #
        # href-ът в HTML-а на НСИ е ОТНОСИТЕЛЕН, но НЕ
        # спрямо текущата страница:
        #
        #   href="sites/default/files/.../HPI_2.1-en.xlsx"
        #
        # На страница /en/statistical-data/98/331 първият
        # опит използва urljoin() и получи:
        #
        #   https://www.nsi.bg/en/statistical-data/98/331/
        #       sites/default/...          -> HTTP 404
        #
        # Правилният адрес е в КОРЕНА на домейна:
        #
        #   https://www.nsi.bg/sites/default/.../HPI_2.1-en.xlsx
        #
        # Разликата е 404 срещу 200 - точно заради нея беше
        # изхарчен една от петте разрешени заявки.
        """
        found: List[Dict[str, str]] = []
        seen: set = set()

        for match in self.DATA_FILE_PATTERN.finditer(html):
            href = match.group(1).strip()

            if href.startswith(("http://", "https://")):
                absolute = href
            elif href.startswith("/"):
                absolute = self.BASE_URL + href
            else:
                # ТОЧНО ТУК е бил източникът на 404-а:
                # "./" и "../" се махат и остава пътят
                # спрямо КОРЕНА, не спрямо страницата.
                absolute = self.BASE_URL + "/" + href.lstrip("./")

            if absolute in seen:
                continue
            seen.add(absolute)

            found.append({
                "url": absolute,
                "title": absolute.rsplit("/", 1)[-1],
                "relative_href": href,
            })

        return found

    def discover_indicators(self) -> Dict[str, Any]:
        """
        Открива XLSX файловете с данни на една страница.

        ВРЪЩА {'page_url', 'indicators_found', 'indicators',
        'diagnostics', 'html_bytes', 'href_extensions'}.

        Ако не намери файлове, diagnostics ОБЯСНЯВА защо.
        """
        url = self.BASE_URL + self.BULGARIAN_PRICE_PAGE

        print(f"[INFO] Reading NSI house price statistics:")
        print(f"[INFO] {url}")

        html = self._get(url).decode(
            "utf-8", errors="replace"
        )

        diagnostics: List[str] = []

        # TAG: FILE LINKS
        #
        # Пълната логика (включително разрешаването на
        # относителните href-ове) е в _parse_data_file_links.
        found: List[Dict[str, str]] = self._parse_data_file_links(html)

        # TAG: WHAT IS ACTUALLY ON THE PAGE
        extensions: Dict[str, int] = {}
        for match in self.ANY_HREF_PATTERN.finditer(html):
            href = match.group(1)
            suffix = re.search(
                r"\.([A-Za-z0-9]{2,5})(?:\?|#|$)",
                href,
            )
            if suffix:
                key = suffix.group(1).lower()
                extensions[key] = extensions.get(key, 0) + 1

        if found:
            diagnostics.append(
                f"Found {len(found)} data file link(s). "
                f"NOTE: hrefs are relative to the DOMAIN root, "
                f"not to the current page."
            )
            for item in found:
                diagnostics.append(
                    f"  {item['title']} <- {item['url']}"
                )
        else:
            diagnostics.append(
                "NO data-file links on this page. Measured: "
                + (
                    ", ".join(
                        f".{ext} x{count}"
                        for ext, count in sorted(
                            extensions.items(),
                            key=lambda kv: -kv[1],
                        )[:6]
                    )
                    or "no hrefs with extensions at all"
                )
            )
            diagnostics.append(
                "Likely a METADATA-ONLY page. Verified on "
                "28.09.2026: /statistical-data/90, /96 and "
                "/en/statistical-data/99 all contain only "
                "descriptions and contact tables - no figures."
            )
            diagnostics.append(
                "Next step: walk to /en/statistical-data/<id>/<subid> "
                "and look for the 'Time series' accordion."
            )

        return {
            "page_url": url,
            "indicators_found": len(found),
            "indicators": found,
            "html_bytes": len(html),
            "href_extensions": extensions,
            "diagnostics": diagnostics,
        }

    # TAG: FILE FETCH
    def fetch_indicator_file(
        self,
        url: str,
        output_dir: Path,
    ) -> Path:
        """
        Сваля ЕДИН файл с данни от nsi.bg.

        Адресът трябва да е от nsi.bg - това се проверява.
        """
        self._assert_allowed(url)

        output_dir.mkdir(parents=True, exist_ok=True)

        filename = url.rsplit("/", 1)[-1]
        output_file = output_dir / filename

        print(f"[INFO] Fetching: {url}")

        payload = self._get(url)

        # Атомен запис - файлът не бива да остане частично
        # записан при прекъсване.
        temporary = output_file.with_suffix(
            output_file.suffix + ".tmp"
        )
        temporary.write_bytes(payload)
        temporary.replace(output_file)

        print(f"[OK] {len(payload):,} bytes -> {output_file}")

        return output_file

    # TAG: FETCH + PARSE + NORMALIZE
    def fetch_and_parse(
        self,
        url: str,
        output_dir: Path,
        indicator_id: str = "nsi",
    ) -> Dict[str, Any]:
        """
        Пълният цикъл за един файл: сваляне -> парс ->
        запис в unified schema.

        Връща обобщение, което ОПИСВА какво е направено и
        какво не е - за         да може човек да реши дали файлът е
        полезен, без да отваря файловете ръчно.
        """
        # TAG: ЯСНА ГРЕШКА ПРИ ОБЪРКАН АРГУМЕНТ
        #
        # При подаден Path urlparse() падаше с
        #   AttributeError: 'WindowsPath' object has no
        #   attribute 'decode'
        # Това не казва нищо на човека. Съветът "искаше ли
        # да парснеш локален файл? -> parse_local_file()"
        # е истинската причина за грешката.
        if not isinstance(url, str):
            raise TypeError(
                f"url must be a string, got "
                f"{type(url).__name__}. Did you mean "
                f"parse_local_file({url!r}, ...)? That variant "
                f"reads an existing file and makes NO request."
            )

        from tools.nsi_parser import (
            parse_nsi_file,
            to_unified_records,
        )

        raw_file = self.fetch_indicator_file(url, output_dir)

        payload = raw_file.read_bytes()

        parsed = parse_nsi_file(raw_file.name, payload)

        records = to_unified_records(
            parsed,
            indicator_id,
        )

        dataset_file = (
            output_dir / f"dataset_{indicator_id}.json"
        )

        dataset_file.write_text(
            json.dumps(
                records,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "raw_file": str(raw_file),
            "raw_bytes": len(payload),
            "dataset_file": str(dataset_file),
            "format": parsed["format"],
            "title": parsed["title"],
            "columns": len(parsed["series"]),
            "records_parsed": parsed["record_count"],
            "records_written": len(records),
            "diagnostics": parsed["diagnostics"],
        }

    def parse_local_file(
        self,
        raw_file: Path,
        output_dir: Path,
        indicator_id: str = "nsi",
    ) -> Dict[str, Any]:
        """
        СЪЩОТО КАТО fetch_and_parse, но БЕЗ мрежа.

        # TAG: ЗАЩО Е НУЖНО ОТДЕЛЕН МЕТОД
        #
        # Реалният HPI_2.1-en.xlsx вече е на диск. Правилно
        # разтълкуването му не изисква нова заявка към НСИ,
        # а повторно сваляне е точно забраненото в условията
        # "без нови сваляния".
        #
        # fetch_and_parse() приема URL и винаги мрежа. Ако
        # му се подаде Path, urlparse() пада с
        # "'WindowsPath' object has no attribute 'decode'" -
        # объркан аргумент вместо ясно съобщение.
        """
        raw_file = Path(raw_file)
        output_dir = Path(output_dir)

        # Първата версия НЕ създаваше папката. При
        # tmp_path-подобен output гръмваше FileNotFoundError
        # чак при записва - след като парсването е приключило,
        # тоест след като е изразходвано цялото време.
        output_dir.mkdir(parents=True, exist_ok=True)

        sys.path.insert(
            0,
            str(Path(__file__).resolve().parent.parent),
        )

        from tools.nsi_parser import (
            parse_nsi_file,
            to_unified_records,
        )

        payload = raw_file.read_bytes()

        parsed = parse_nsi_file(raw_file.name, payload)

        records = to_unified_records(parsed, indicator_id)

        dataset_file = output_dir / f"dataset_{indicator_id}.json"

        dataset_file.write_text(
            json.dumps(
                records,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "raw_file": str(raw_file),
            "raw_bytes": len(payload),
            "dataset_file": str(dataset_file),
            "format": parsed["format"],
            "title": parsed["title"],
            "columns": len(parsed["series"]),
            "records_parsed": parsed["record_count"],
            "records_written": len(records),
            "diagnostics": parsed["diagnostics"],
        }

    # TAG: COLLECT CONTRACT
    def collect(self) -> Path:
        """
        Записва каталога на показателите в
        storage_raw/nsi/<DD-MM-YYYY_HH>/nsi_indicators.json
        """
        now = datetime.now(TIMEZONE)
        date_folder = now.strftime("%d-%m-%Y_%H")

        output_dir = self.raw_dir / date_folder
        output_dir.mkdir(parents=True, exist_ok=True)

        discovery = self.discover_indicators()

        payload = {
            "source": self.SOURCE_NAME,
            "collected_at": now.isoformat(),
            "base_url": self.BASE_URL,
            "citation_required": (
                "Източник: Национален статистически институт "
                "(НСИ), Статистика на цените на жилища."
            ),
            "legal_note": (
                "Официална държавна статистика. robots.txt "
                "разрешава автоматично четене на публичното "
                "съдържание (забранен е само /admin/)."
            ),
            "data_basis": (
                "Индексите се изчисляват от нотариални сделки - "
                "пълни цени, включващи стойността на земята."
            ),
            **discovery,
        }

        output_file = output_dir / "nsi_indicators.json"

        output_file.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print()
        print(
            f"[OK] Found {discovery['indicators_found']} "
            f"data file(s)"
        )
        print(f"[OK] Saved to: {output_file}")
        print()
        print("[NOTE] NSI has no REST API for this series.")
        print("[NOTE] To download a table, pass its real URL:")
        print("[NOTE]   python -m collector.nsi <full-url>")

        return output_file


# TAG: CLI

if __name__ == "__main__":
    import sys

    base = Path(__file__).resolve().parent.parent
    raw = base / "storage_raw"

    collector = NsiCollector(raw)

    if len(sys.argv) > 1:
        now = datetime.now(TIMEZONE)
        target = raw / "nsi" / now.strftime("%d-%m-%Y_%H")

        url = sys.argv[1]
        indicator_id = (
            sys.argv[2] if len(sys.argv) > 2 else "nsi"
        )

        summary = collector.fetch_and_parse(
            url,
            target,
            indicator_id,
        )

        print()
        print("[SUMMARY]")
        for key, value in summary.items():
            if key == "diagnostics" and not value:
                continue
            print(f"  {key:16}: {value}")
    else:
        collector.collect()
