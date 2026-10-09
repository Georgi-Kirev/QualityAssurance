# TAG: TESTS - SOURCE REGISTRY
#
# Регистърът е ДОКУМЕНТ за решения, а не просто dict. Тези
# тестове пазят най-скъпото в него: че източник, маркиран
# като забранен или платен, не може да бъде използван, дори
# ако някой после добави URL в кода.
#
# Реалната стойност на този файл е, че след 6 месеца някой
# (включително аз) няма да опита да скрейпне забранен сайт.

import pytest

from collector.registry import (
    AccessLevel,
    SourceSpec,
    SOURCES,
    blocked_sources,
    get_source,
    legal_sources,
    register_currency_defaults,
    report,
    usable_now,
)


# ============================================================
# TAG: REGISTRY INTEGRITY
# ============================================================

def test_registry_is_not_empty():
    assert len(SOURCES) >= 5


def test_every_source_has_legal_note():
    """
    Всяко решение трябва да е обосновано. Иначе след време
    никой няма да помни защо нещо е било отхвърлено.
    """
    for key, spec in SOURCES.items():
        assert spec.legal_note.strip(), f"{key} has no legal_note"
        assert spec.license_note.strip(), f"{key} has no license_note"


def test_every_source_has_currency():
    for key, spec in SOURCES.items():
        assert spec.currency, f"{key} has no currency"


def test_every_source_has_dataset_description():
    for key, spec in SOURCES.items():
        assert spec.dataset.strip(), f"{key} has no dataset description"


def test_access_levels_are_known():
    valid = {
        AccessLevel.OPEN_API,
        AccessLevel.OPEN_DATA,
        AccessLevel.HTML_ONLY,
        AccessLevel.PAID,
        AccessLevel.FORBIDDEN,
        AccessLevel.UNVERIFIED,
    }
    for key, spec in SOURCES.items():
        assert spec.access in valid, f"{key} has unknown access level"


def test_verified_sources_have_a_date():
    for key, spec in SOURCES.items():
        if spec.verified_ok:
            assert spec.verified_on, (
                f"{key} is marked verified but has no date"
            )


def test_get_source_is_case_insensitive():
    assert get_source("SofiaPlan") is not None
    assert get_source("  EGOV  ") is not None
    assert get_source("does-not-exist") is None


# ============================================================
# TAG: THE LEGAL INVARIANTS
# ============================================================

def test_forbidden_sources_never_appear_in_legal_list():
    """
    ОСНОВНИЯТ тест. Скрейпване на източник със забранени
    условия е нарушение на ЗЗПД, независимо че страницата
    е публична.
    """
    legal_keys = {s.key for s in legal_sources()}

    for spec in SOURCES.values():
        if spec.access == AccessLevel.FORBIDDEN:
            assert spec.key not in legal_keys, (
                f"{spec.key} is FORBIDDEN but shows up as legal"
            )


def test_forbidden_sources_never_appear_in_usable():
    usable_keys = {s.key for s in usable_now()}

    for spec in SOURCES.values():
        if spec.access in (
            AccessLevel.FORBIDDEN,
            AccessLevel.PAID,
        ):
            assert spec.key not in usable_keys


def test_paid_sources_are_blocked():
    blocked_keys = {s.key for s in blocked_sources()}

    for spec in SOURCES.values():
        if spec.access == AccessLevel.PAID:
            assert spec.key in blocked_keys


def test_terramap_is_forbidden_with_evidence():
    """
    TERRAMAP.BG цитира дословно забрана в условията си.
    Това е проверка, че решението е документирано, а не
    просто 'не ни хареса'.
    """
    spec = get_source("terramap")

    assert spec is not None
    assert spec.access == AccessLevel.FORBIDDEN
    assert "скрейп" in spec.legal_note.lower()


def test_property_register_is_paid_not_forbidden():
    """
    Имотният регистър не е ЗАБРАНЕН - той е ПЛАТЕН. Това е
    различна правна ситуация и различно решение.
    """
    spec = get_source("property_register")

    assert spec.access == AccessLevel.PAID
    assert "такса" in spec.legal_note.lower()


def test_listing_sites_are_blocked():
    spec = get_source("listing_sites")

    assert spec.access == AccessLevel.FORBIDDEN
    blocked = {s.key for s in blocked_sources()}
    assert "listing_sites" in blocked


# ============================================================
# TAG: USABLE SOURCES
# ============================================================

def test_sofiaplan_is_usable():
    spec = get_source("sofiaplan")

    assert spec.access == AccessLevel.OPEN_API
    assert spec.verified_ok
    assert spec.currency == "BGN"


def test_egov_and_nsi_are_usable():
    for key in ("egov", "nsi"):
        spec = get_source(key)
        assert spec is not None
        assert spec.access == AccessLevel.OPEN_DATA
        assert spec.verified_ok


def test_usable_now_excludes_unverified():
    """
    UNVERIFIED != използваем. Ако не е доказано, че е
    достъпно, не се брои.
    """
    usable_keys = {s.key for s in usable_now()}

    for spec in SOURCES.values():
        if spec.access == AccessLevel.UNVERIFIED:
            assert spec.key not in usable_keys


def test_usable_set_is_small_and_real():
    """
    Ако някой днес добави 20 'източника', това трябва да е
    видимо подозрително. Малък брой = качествен анализ.
    """
    assert 2 <= len(usable_now()) <= 5


# ============================================================
# TAG: CURRENCY REGISTRATION
# ============================================================

def test_register_currency_defaults_runs():
    register_currency_defaults()


def test_source_currencies_land_in_currency_module():
    register_currency_defaults()

    from tools import currency

    # Всички български държавни източници трябва да са BGN,
    # за да не се третират лева като евро (бъгът от 28.09).
    for key in ("sofiaplan", "egov", "nsi"):
        assert currency.source_currency(key) == "BGN"


# ============================================================
# TAG: REPORT
# ============================================================

def test_report_is_generated():
    text = report()

    assert "ИЗТОЧНИЦИ НА ДАННИ" in text
    assert "sofiaplan" in text
    assert "FORBIDDEN" in text


def test_report_explains_blocked_sources():
    text = report().lower()

    assert "не може" in text
    assert "terramap" in text
