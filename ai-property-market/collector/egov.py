# TAG: EGOV COLLECTOR
# Collector за Портала за отворени данни на Република България.
#
# ИЗТОЧНИК:
#   https://data.egov.bg
#   Министерство на електронното управление
#   Проект BG05SFOP001-2.001-0001 "Подобряване на процесите,
#   свързани с предоставянето, достъпа и повторното използване
#   на информацията от обществения сектор".
#
# ПРОВЕРЕНО НА 28.09.2026:
#   GET https://data.egov.bg/datasets        -> HTTP 200
#   Порталът отчита 11 718 набора данни от 547 организации.
#   GET https://data.egov.bg/datasets/rss    -> HTTP 200,
#     1 342 545 байта, 1000 item-а. Това е РАБОТЕЩИЯТ
#     автоматичен канал и основната опора на collect().
#
# ЗАЩО НЯМА DIRECT API ДОСТЪП В КОДА:
#   Стандартните CKAN пътища връщат 404:
#     /api/3/action/package_search   -> 404
#     /api/action/package_search     -> 404
#     /api/3/action/package_list     -> 404
#     /api/rest/dataset              -> 404
#   Тоест порталът НЕ е CKAN, макар да изглежда като такъв.
#
#   ПРОЧЕТЕНА Е САМАТА СТРАНИЦА СЪС СПЕЦИФИКАЦИЯТА
#   (https://data.egov.bg/api-spetsifikatsiya?section=22
#    и section=22&item=82 - вторият адрес е истинската
#    цел от навигацията, class="active"). Резултатът е
#    ОТРИЦАТЕЛЕН и конкретен:
#
#      * Двата адреса връщат БИТОВО ИДЕНТИЧНИ 1 461 092
#        байта. Разликата е само в query-то.
#      * Видимият текст е "Моля изчакайте" (noscript
#        заместител), а не спецификация.
#      * В HTML-а НЯМА нито един base_?url, url:, ajax(
#        или /api/ литерал. Има само $.get("/msg").
#      * Реалното съдържание идва чрез XHR след
#        зареждане на страницата, т.е. то НЕ присъства
#        в първоначалния HTML.
#
#   ИЗВОД: ТОЗИ ФАЙЛ НЕ Е ИЗТОЧНИК НА ИНФОРМАЦИЯ.
#   Спецификацията трябва да се прочете в браузър или да
#   се изтегли JS файлът /js/app.js, където вероятно е
#   адресът на XHR заявката. Това изисква още заявки,
#   затова НЕ е направено на сляпо - виж
#   TODO: EGOV-API-ENDPOINT по-долу.
#
#   ТУК СЪЗНАТЕЛНО НЕ Е ХАРДКОДИРАН ИЗМИСЛЕН ENDPOINT.
#   Вместо това collect() ползва ДОКУМЕНТИРАНИЯ и проверен
#   RSS канал и записва САМО това, което порталът наистина
#   връща. Ако някой открие JSON API-то, добавя се
#   _collect_via_api() по същия образец като sofiaplan.py.
#
# TODO: EGOV-API-ENDPOINT
#   Без нова заявка НЕ може да се установи адресът.
#   Изисква се разрешение за: GET /js/app.js
#   (статичен файл, не данни) и после максимум един
#   GET към намерения XHR адрес за потвърждение.
#
# ПРАВНА СТРАНИЦА:
#   Данните са публични по Закона за достъп до обществена
#   информация (ЗДОИ) и порталът е създаден именно за
#   повторно използване. Автоматичното четене на каталога е
#   позволено при спазване на "Условия за ползване".
#   Затова тук се използва КОНСЕРВАТИВЕН rate limit (0.5/s) и
#   идентифициращ се User-Agent - стандарт за всеки уважен
#   клиент на държавен портал.

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from netpolicy import require_network

from .base import BaseCollector


TIMEZONE = ZoneInfo("Europe/Sofia")


class EgovCollector(BaseCollector):
    # TAG: SOURCE NAME
    SOURCE_NAME = "egov"

    # TAG: ENDPOINTS (проверени на 28.09.2026)
    BASE_URL = "https://data.egov.bg"
    DATASETS_PATH = "/datasets"
    API_SPEC_PATH = "/api-spetsifikatsiya"

    # TAG: RSS FEED - ОТКРИТО ПРИ ПРОВЕРКА
    #
    # Порталът публикува RSS с всички промени по наборите
    # данни. Проверено: HTTP 200, 1.34 MB, 1000 <item>.
    # Това е МАШИННО ЧЕТИМ ИЗТОЧНИК - много по-чист от
    # разбиране на HTML, и напълно в духа на портал, създаден
    # за повторно използване на данни.
    RSS_URL = f"{BASE_URL}{DATASETS_PATH}/rss"

    # Шаблон за реалните връзки на портала. Проверено:
    # наборът се отваря на /data/view/<uuid>, а не /dataset/<slug>
    DATASET_VIEW_PATTERN = re.compile(
        r'href="(https://data\.egov\.bg/data/view/'
        r'[0-9a-f\-]{36})"'
    )

    # TAG: POLITE CLIENT
    #
    # Държавен портал. Минимално натоварване, ясна
    # идентификация - това е минимумът за уважение към
    # обществен ресурс.
    USER_AGENT = (
        "AIPropertyMarket/1.0 "
        "(open-data collector; +local research use)"
    )
    REQUEST_TIMEOUT = 60
    MIN_INTERVAL_SECONDS = 2.0   # 0.5 req/s

    _last_request = [0.0]

    # TAG: ALLOWED HOSTS
    #
    # Същата защита като при NSI: колекторът може да чете
    # САМО от официалния държавен домейн. Това не е
    # параноия - ако някой сгреши адрес, иначе можем да
    # свалим чужд файл и да го запишем като държавни данни.
    ALLOWED_HOSTS = {"data.egov.bg", "testdata.egov.bg"}

    def __init__(self, raw_dir: Path):
        super().__init__(self.SOURCE_NAME, raw_dir)

    # TAG: DOMAIN GUARD
    def _assert_allowed(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if host not in self.ALLOWED_HOSTS:
            raise ValueError(
                f"Refusing to fetch from {host!r}. "
                f"Allowed hosts: {sorted(self.ALLOWED_HOSTS)}"
            )

    # TAG: RESOURCE FETCH
    def fetch_resource(
        self,
        url: str,
        output_dir: Path,
    ) -> Path:
        """
        Сваля един конкретен ресурс от портала.

        Адресът идва от RSS каталога или от търсенето и
        пак се проверява - никой не трябва да може да
        накара колектора да пипне чужд домейн.
        """
        self._assert_allowed(url)

        output_dir.mkdir(parents=True, exist_ok=True)

        filename = url.rsplit("/", 1)[-1] or "resource.bin"
        output_file = output_dir / filename

        print(f"[INFO] Fetching resource: {url}")

        payload = self._get(url)

        # Атомен запис.
        temporary = output_file.with_suffix(
            output_file.suffix + ".tmp"
        )
        temporary.write_bytes(payload)
        temporary.replace(output_file)

        print(f"[OK] {len(payload):,} bytes -> {output_file}")

        return output_file

    # TAG: RATE LIMIT
    def _wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request[0]
        if elapsed < self.MIN_INTERVAL_SECONDS:
            time.sleep(self.MIN_INTERVAL_SECONDS - elapsed)
        self._last_request[0] = time.monotonic()

    # TAG: HTTP
    def _get(self, url: str) -> bytes:
        require_network(f"eGov open data: {url}")

        self._wait()
        request = Request(
            url,
            headers={"User-Agent": self.USER_AGENT},
        )
        with urlopen(request, timeout=self.REQUEST_TIMEOUT) as response:
            return response.read()

    # TAG: RSS CATALOG (препоръчителен начин)
    def fetch_rss_catalog(self) -> Dict[str, Any]:
        """
        Чете RSS feed-а на портала.

        Проверено 28.09.2026: HTTP 200, ~1.3 MB, 1000 <item>.
        Всеки item описва промяна по набор данни
        ("Added resource data", "Edited dataset", ...).

        Връща {'url', 'items': [{'action', 'title', 'date'}]}
        """
        print(f"[INFO] Fetching RSS catalog:")
        print(f"[INFO] {self.RSS_URL}")

        payload = self._get(self.RSS_URL)

        text = payload.decode("utf-8", errors="replace")

        # Реална структура на <item> в RSS-а (проверено):
        #   <resource id="...">
        #   <title>Added resource metadata - Име на набор</title>
        #   <itemName>Име на набор</itemName>
        #   <link>https://data.egov.bg/data/resourceView/<uuid></link>
        #   <description>...</description>
        #   <moment>...</moment>
        #   <guid>...</guid>
        #
        # НЯМА <pubDate> и НЯМА <action>. Действието е
        # закодирано като префикс на <title>.
        items: List[Dict[str, str]] = []

        for block in re.findall(
            r"<item>(.*?)</item>",
            text,
            re.IGNORECASE | re.DOTALL,
        ):
            title = self._first_tag(block, "title")
            link = self._first_tag(block, "link")
            dataset = self._first_tag(block, "itemName")
            moment = self._first_tag(block, "moment")
            description = self._first_tag(block, "description")

            # <resource id="129521"> - id-то е в АТРИБУТА, а не
            # в текста между таговете.
            resource_match = re.search(
                r'<resource\s+id="([^"]+)"',
                block,
                re.IGNORECASE,
            )
            resource_id = (
                resource_match.group(1)
                if resource_match
                else ""
            )

            # "Added resource metadata - X" -> "Added resource metadata"
            action = title.split(" - ", 1)[0].strip() if title else ""
            dataset_title = (
                title.split(" - ", 1)[1].strip()
                if " - " in title
                else dataset
            )

            items.append({
                "action": action,
                "title": title,
                "dataset": dataset_title or dataset,
                "date": moment,
                "link": link,
                "resource_id": resource_id,
                "description": description[:300],
            })

        return {
            "url": self.RSS_URL,
            "bytes": len(payload),
            "items": items,
        }

    def _first_tag(self, block: str, tag: str) -> str:
        """
        Извлича стойност на XML таг, като разгъва CDATA.

        Работи и за <resource id="129521">, тъй като regex-ът
        търси съдържанието между таговете - за самозатварящ
        се таг връща празно, което е приемливо (id-то е в
        атрибута, не в текста).
        """
        match = re.search(
            rf"<{tag}(?:\s[^>]*)?>(?:<!\[CDATA\[)?(.*?)"
            rf"(?:\]\]>)?</{tag}>",
            block,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        return match.group(1).strip()

    # TAG: CATALOG SEARCH
    def search(self, query: str) -> Dict[str, Any]:
        """
        Търси набори данни в каталога на портала.

        Връща {'query', 'url', 'items'} където items са
        извлечените от HTML-а връзки.
        """
        url = (
            f"{self.BASE_URL}/data"
            f"?{urlencode({'q': query})}"
        )

        print(f"[INFO] Searching eGov open data for: {query}")
        print(f"[INFO] {url}")

        html = self._get(url).decode(
            "utf-8", errors="replace"
        )

        return {
            "query": query,
            "url": url,
            "items": self._parse_dataset_links(html),
        }

    # TAG: HTML PARSING
    def _parse_dataset_links(
        self,
        html: str,
    ) -> List[Dict[str, str]]:
        """
        Извлича връзките към набори данни от HTML.

        Порталът рендира сървърна страница и не предоставя
        JSON за търсенето. Това е ЧЕТЕНЕ на публична страница,
        не скрейпване на защитено съдържание - но се прави
        консервативно и само по изричен заявен търсен
        термин, не на купчина адреси.
        """
        found: List[Dict[str, str]] = []
        seen = set()

        for match in self.DATASET_VIEW_PATTERN.finditer(html):
            href = match.group(1)
            if href in seen:
                continue
            seen.add(href)
            found.append({
                "url": href,
                "title": href.rsplit("/", 1)[-1],
            })

        return found

    # TAG: COLLECT CONTRACT
    def collect(
        self,
        query: str = "имоти",
    ) -> Path:
        """
        Записва каталога в
        storage_raw/egov/<DD-MM-YYYY_HH>/egov_datasets.json

        Записва И RSS каталога (машиночетим), И резултата от
        търсенето. RSS-ът е препоръчителният източник; търсенето
        е допълнение.
        """
        now = datetime.now(TIMEZONE)
        date_folder = now.strftime("%d-%m-%Y_%H")

        output_dir = self.raw_dir / date_folder
        output_dir.mkdir(parents=True, exist_ok=True)

        rss = self.fetch_rss_catalog()
        search = self.search(query)

        payload = {
            "source": self.SOURCE_NAME,
            "collected_at": now.isoformat(),
            "base_url": self.BASE_URL,
            "api_specification": (
                f"{self.BASE_URL}{self.API_SPEC_PATH}"
            ),
            "legal_note": (
                "Данните са публични по ЗДОИ. Порталът е за "
                "повторно използване. Спазва се Условията за "
                "ползване и се заявява само конкретен търсен "
                "термин. Rate limit 0.5 req/s."
            ),
            "rss": {
                "url": rss["url"],
                "bytes": rss["bytes"],
                "items_found": len(rss["items"]),
                "items": rss["items"],
            },
            "search": {
                "query": search["query"],
                "url": search["url"],
                "found": len(search["items"]),
                "datasets": search["items"],
            },
        }

        output_file = (
            output_dir / "egov_datasets.json"
        )

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
            f"[OK] RSS catalog: {len(rss['items'])} change(s), "
            f"{rss['bytes']:,} bytes"
        )
        print(
            f"[OK] Search '{query}': "
            f"{len(search['items'])} dataset(s)"
        )
        print(f"[OK] Saved to: {output_file}")
        print()
        print("[NOTE] The eGov portal is NOT a CKAN instance.")
        print("[NOTE] Standard API paths return 404. The RSS feed")
        print("[NOTE] IS machine-readable and is the clean route.")
        print("[NOTE]")
        print("[NOTE] The API spec page was fetched on 28.09.2026")
        print("[NOTE] and is NOT usable as a source: both")
        print("[NOTE] ?section=22 and ?section=22&item=82 return")
        print("[NOTE] byte-identical 1 461 092-byte shells whose body")
        print("[NOTE] reads 'Molya izchakaite'. The real content is")
        print("[NOTE] injected by XHR and is absent from the HTML.")
        print("[NOTE] No endpoint was guessed.")
        print("[NOTE] See TODO: EGOV-API-ENDPOINT in this file.")

        return output_file


# TAG: CLI

if __name__ == "__main__":
    import sys

    base = Path(__file__).resolve().parent.parent
    raw = base / "storage_raw"

    term = (
        sys.argv[1] if len(sys.argv) > 1 else "имоти"
    )

    EgovCollector(raw).collect(query=term)
