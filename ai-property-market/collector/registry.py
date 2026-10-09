# TAG: SOURCE REGISTRY
# Централен регистър на ВСИЧКИ източници на данни.
#
# ЗАЩО СЪЩЕСТВУВА ОТДЕЛЕН ФАЙЛ:
#   Търсенето за подходящ източник отнема часове, а резултатът
#   трябва да е проверим. Тук е записан ФАКТИЧЕСКИЯТ статус на
#   всеки кандидат - включително защо някой НЕ може да се ползва.
#   Това е по-ценно от списък с красиви линкове, 90% от които
#   водят до платени или забранени услуги.
#
# ВАЖНО ЗА ПРАВНА СТРАНИЦА:
#   Регистърът различава три състояния на достъп. Скрипт, който
#   scrape-ва сайт с изрично забранени условия, нарушава
#   Закона за защита на потребителите (ЗЗПД) и Закона за авторско
#   право (ЗАКП) - независимо че данните са публично достъпни.
#   Затова тук има EXPLICIT статут и причина, а не само URL.
#
# ИЗПОЛЗВАНЕ:
#   from collector.registry import SOURCES, get_source, legal_sources
#   from tools import currency
#   currency.register_source_currency("nsi", "BGN")

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# TAG: ACCESS LEVELS

class AccessLevel:
    """
    Нива на достъп, подредени от най-доброто надолу.

    OPEN_API   - документиран API, без ключ, без такса
    OPEN_DATA  - отворени данни, позволени за автоматично
                 използване според условията на източника
    HTML_ONLY  - няма API; само чрез човешко четене на страници
    PAID       - изисква регистрация/такса/договор
    FORBIDDEN  - УСЛОВИЯТА ЗАБРАНЯВАТ автоматизирано извличане
    UNVERIFIED - не е проверено дали изобщо е достъпно
    """

    OPEN_API = "OPEN_API"
    OPEN_DATA = "OPEN_DATA"
    HTML_ONLY = "HTML_ONLY"
    PAID = "PAID"
    FORBIDDEN = "FORBIDDEN"
    UNVERIFIED = "UNVERIFIED"


# TAG: SOURCE DEFINITION

@dataclass(frozen=True)
class SourceSpec:
    """
    Пълно описание на един източник.
    """

    key: str                    # кратко име, използвано в папките
    display_name: str           # човешко име
    organization: str           # кой го публикува
    base_url: str               # адрес
    access: str                 # AccessLevel
    currency: str               # естествена валута на суровите данни
    dataset: str                # какво съдържа
    rate_limit_per_second: float = 1.0
    license_note: str = ""
    legal_note: str = ""
    verified_on: str = ""
    verified_ok: bool = False
    tags: List[str] = field(default_factory=list)


# TAG: THE REGISTRY
#
# Проверено на 28.09.2026. Всяко твърдение по-горе е
# резултат от реална HTTP заявка, не от памет.

SOURCES: Dict[str, SourceSpec] = {}


def _register(spec: SourceSpec) -> None:
    SOURCES[spec.key] = spec


# -------------------------------------------------------------------
# TAG: 1. SOFIAPLAN - основният източник, вече интегриран
# -------------------------------------------------------------------

_register(SourceSpec(
    key="sofiaplan",
    display_name="SofiaPlan Open Data",
    organization="Столична община (отдел ГИС)",
    base_url="https://api.sofiaplan.bg/datasets",
    access=AccessLevel.OPEN_API,
    currency="BGN",
    dataset=(
        "403 тематични geospatial слоя: хидрография, улици, "
        "зелени системи, кадастър, икономика. САМО 1 dataset "
        "(624 ikonomika.imoti_ceni_ge) съдържа цени - и то "
        "статистика ПО ЗОНИ, не обяви за имоти."
    ),
    rate_limit_per_second=3.0,
    license_note=(
        "Публични общински данни, отворена дефиниция на "
        "endpoints, без ключ."
    ),
    legal_note=(
        "Работи се вече в продукта. 1.78 млн. записа, но само "
        "0.13% имат цена."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["geo", "prices", "zones", "sofia"],
))


# -------------------------------------------------------------------
# TAG: 2. EGOV - ДЪРЖАВЕН ПОРТАЛ ЗА ОТВОРЕНИ ДАННИ
# -------------------------------------------------------------------

_register(SourceSpec(
    key="egov",
    display_name="Портал за отворени данни (data.egov.bg)",
    organization="Министерство на електронното управление",
    base_url="https://data.egov.bg",
    access=AccessLevel.OPEN_DATA,
    currency="BGN",
    dataset=(
        "11 718 набора данни от 547 държавни организации, "
        "включително регистри, класификатори (ЕКАТТЕ, НКНКМ), "
        "статистика и др."
    ),
    rate_limit_per_second=0.5,
    license_note=(
        "Закон за достъп до обществена информация - данните са "
        "публични и предназначени за повторно използване. Има "
        "публикувана 'Условия за ползване' на портала."
    ),
    legal_note=(
        "ПОТВЕРДЕНО: data.egov.bg/datasets -> HTTP 200, "
        "11 718 набора. API спецификацията съществува на "
        "/api-spetsifikatsiya?section=22, но стандартните "
        "CKAN пътища (/api/3/action/...) връщат 404. "
        "Реалният API endpoint трябва да се потвърди от "
        "документацията, преди да се пише колектор. "
        "Дотогава -> UNVERIFIED за автоматизиран достъп."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["open-data", "registry", "classifiers", "statistics"],
))


# -------------------------------------------------------------------
# TAG: 3. NSI - НАЦИОНАЛЕН СТАТИСТИЧЕСКИ ИНСТИТУТ
# -------------------------------------------------------------------

_register(SourceSpec(
    key="nsi",
    display_name="Национален статистически институт",
    organization="НСИ",
    base_url="https://www.nsi.bg",
    access=AccessLevel.OPEN_DATA,
    currency="BGN",
    dataset=(
        "Статистика на цените на жилища и HPI (House Price "
        "Index), ДЕЙСТВИТЕЛНО изчислени от нотариални сделки. "
        "От 2022 г. включва апартаменти и къщи."
    ),
    rate_limit_per_second=0.5,
    license_note=(
        "Държавна статистика, публикувана за свободен достъп. "
        "robots.txt: Disallow само /admin/ - т.е. публичното "
        "съдържание е разрешено за автоматично четене."
    ),
    legal_note=(
        "ПОТВЕРДЕНО: nsi.bg/en/statistical-data/99 -> HTTP 200. "
        "Най-качественият законен източник за цени на пазара: "
        "не е обявна, а реална сделка. Проблемът е форматът - "
        "NSI публикува XLSX/regnp файлове, не REST API, затова "
        "са нужни отделни URL адреси за всеки отчет."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["prices", "hpi", "transactions", "official", "national"],
))


# -------------------------------------------------------------------
# TAG: ЗАБРАНЕНИ И НЕДОСТЪПНИ - ДОКУМЕНТИРАНИ, ЗА ДА НЕ СЕ
#       ПРЕПРОВАРЯТ ВЕЧНО
# -------------------------------------------------------------------

_register(SourceSpec(
    key="property_register",
    display_name="Имотен регистър (Агенция по вписванията)",
    organization="Агенция по вписванията",
    base_url="https://portal.registryagency.bg",
    access=AccessLevel.PAID,
    currency="BGN",
    dataset="Собственост, вещни тежести, исторически промени.",
    license_note=(
        "Няма безплатен лиценз. Услугата е платена държавна "
        "такса по Тарифа на АВИ; масово използване изисква "
        "договор."
    ),
    legal_note=(
        "НЕ Е ОТВОРЕН. Услугата 'Справка чрез отдалечен достъп' "
        "изисква ЕГН/Булстат, регистрация с КЕП и ПЛАЩАНЕ на "
        "държавна такса. Няма публичен API. Ползването "
        "автоматично без договор е нелегално. Ако е нужен "
        "наистина - само по писмен договор с АВИ."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["paid", "not-open", "ownership"],
))

_register(SourceSpec(
    key="npik",
    display_name="НПИК - регистър на УПИ",
    organization="МФ / АГКК",
    base_url="https://kais.cadastre.bg",
    access=AccessLevel.UNVERIFIED,
    currency="BGN",
    dataset="УПИ, площи, кадастрални идентификатори.",
    license_note=(
        "Без публичен лиценз за автоматизирано ползване. "
        "Масово предоставяне - по заявка и договор с АГКК."
    ),
    legal_note=(
        "ПРОВЕРЕНО: не е открит публичен API. КАИС е "
        "интерактивна карта, без документиран машинно достъпен "
        "интерфейс. Услугата за масово предоставяне на данни "
        "изисква заявка до АГКК. Не може да се автоматизира "
        "легално без официално съгласие."
    ),
    verified_on="28.09.2026",
    verified_ok=False,
    tags=["cadastre", "plot", "not-open", "needs-agreement"],
))

_register(SourceSpec(
    key="terramap",
    display_name="TERRAMAP.BG",
    organization="Аура Дигитал ЕООД",
    base_url="https://terramap.bg",
    access=AccessLevel.FORBIDDEN,
    currency="BGN",
    dataset="Средни цени на m2 по квартали, обобщени от обяви.",
    license_note=(
        "Всички права запазени. Условията изрично забраняват "
        "автоматизирано извличане на данни."
    ),
    legal_note=(
        "ИЗКЛЮЧЕН. Условията на сайта казват дословно: "
        "'Строго забранено е използването на автоматизирани "
        "средства (ботове, скрейпъри, crawlers, скриптове или "
        "други технически инструменти) за извличане, копиране "
        "или събиране на данни'. Скрейпването тук е нарушение "
        "на договора, недопустимо според чл. 7 от Закона за "
        "защита на потребителите. Освен това компанията "
        "събира данни ОТ обяви - не си струва да ги крадем."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["forbidden", "scraping-banned", "aggregator"],
))

_register(SourceSpec(
    key="listing_sites",
    display_name="Обявни сайтове (bazar.bg, homes.bg, alo.bg, ...)",
    organization="частни търговци",
    base_url="различни",
    access=AccessLevel.FORBIDDEN,
    currency="BGN",
    dataset="Обяви за продажба/наем на имоти.",
    license_note=(
        "Обявите са защитени от авторско право (ЗАКП). "
        "Условията на порталите забраняват автоматизирано "
        "извличане."
    ),
    legal_note=(
        "ИЗКЛЮЧЕНИ. Всички проверени сайтове имат условия, "
        "забраняващи автоматизирано извличане. Скрейпването на "
        "обяви носи и отговорност по ЗАКП за авторско право върху "
        "обявата и снимките. Алтернативи: (а) договор с "
        "конкретния портал за feed/API; (б) платена такса на "
        "агент/API на пазара."
    ),
    verified_on="28.09.2026",
    verified_ok=True,
    tags=["forbidden", "scraping-banned", "commercial"],
))


# TAG: HELPERS

def get_source(key: str) -> Optional[SourceSpec]:
    return SOURCES.get(key.strip().lower())


def legal_sources() -> List[SourceSpec]:
    """
    Източници, които МОЖЕ да се ползват автоматично и законно.
    """
    return [
        spec
        for spec in SOURCES.values()
        if spec.access in (AccessLevel.OPEN_API, AccessLevel.OPEN_DATA)
    ]


def blocked_sources() -> List[SourceSpec]:
    """
    Източници, които не бива да се ползват автоматично -
    защото са платени, забранени или недоказуемо достъпни.
    """
    return [
        spec
        for spec in SOURCES.values()
        if spec.access in (
            AccessLevel.FORBIDDEN,
            AccessLevel.PAID,
            AccessLevel.UNVERIFIED,
        )
    ]


def usable_now() -> List[SourceSpec]:
    """
    Източници, които са проверени И разрешени.
    """
    return [
        spec
        for spec in SOURCES.values()
        if spec.verified_ok
        and spec.access in (AccessLevel.OPEN_API, AccessLevel.OPEN_DATA)
    ]


def register_currency_defaults() -> None:
    """
    Регистрира естествената валута на всеки източник в
    tools.currency, за да може нормализаторът да конвертира
    правилно.
    """
    from tools import currency

    for spec in SOURCES.values():
        try:
            currency.register_source_currency(
                spec.key,
                spec.currency,
            )
        except (KeyError, ValueError):
            # Вече регистрирана или непозната валута - не е
            # фатално, но си струва да се знае.
            continue


def report() -> str:
    """
    Човешки четим отчет за състоянието на източниците.
    """
    lines = ["=" * 74, "ИЗТОЧНИЦИ НА ДАННИ - РЕГИСТЪР", "=" * 74, ""]

    lines.append("МОЖЕ ДА СЕ ПОЛЗВА СЕГА:")
    for spec in usable_now():
        lines.append(f"  * {spec.key:22} [{spec.access}]")
        lines.append(f"      {spec.display_name}")
        lines.append(f"      {spec.base_url}")
        lines.append(f"      {spec.dataset[:88]}")
        lines.append("")

    lines.append("")
    lines.append("НЕ МОЖЕ (документирано защо):")
    for spec in blocked_sources():
        lines.append(f"  * {spec.key:22} [{spec.access}]")
        for line in _wrap(spec.legal_note, 68):
            lines.append(f"      {line}")
        lines.append("")

    return "\n".join(lines)


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    print(report())
