# TAG: TESTS - CURRENCY
# Тестове за tools/currency.py и валутното поведение на
# normalizer / consolidator / finished_exporter.
#
# Тук се пази бъгът, открит на 28.09.2026: SofiaPlan публикува
# цени в ЛЕВА, а detect_price() етикетираше всяко поле без
# валутен суфикс като EUR. Това удвояваше всеки ценови запис.

import pytest

from tools import currency


# ============================================================
# TAG: BGN -> EUR
# ============================================================

def test_bgn_to_eur_uses_fixed_central_bank_rate():
    # 1 EUR = 1.95583 BGN (фиксиран курс от 1999 г.)
    assert currency.to_eur(1.95583, "BGN") == pytest.approx(1.0)


def test_sofiaplan_zone_price_is_converted_not_relabelled():
    """
    РЕГРЕСИЯ: 116 813.08 беше записано като value_eur, но е BGN.
    Реалната стойност в евро е ~59 725.62.
    """
    result = currency.to_eur(116813.08, "BGN")

    assert result == pytest.approx(59725.62, rel=1e-4)
    assert result < 116813.08 / 1.9


def test_eur_to_eur_is_identity():
    assert currency.to_eur(1234.56, "EUR") == pytest.approx(1234.56)


def test_bgn_is_roughly_half_of_eur_amount():
    # 195 583 BGN трябва да е точно 100 000 EUR.
    assert currency.to_eur(195583.0, "BGN") == pytest.approx(
        100000.0, rel=1e-6
    )


# ============================================================
# TAG: USD
# ============================================================

def test_usd_is_supported():
    assert currency.is_supported("USD")
    assert "USD" in currency.supported_currencies()


def test_usd_conversion_roundtrip():
    eur = currency.to_eur(1000, "USD")
    back = currency.from_eur(eur, "USD")

    assert back == pytest.approx(1000.0, rel=1e-6)


def test_usd_is_more_than_eur():
    # 1 EUR трябва да е ~1.08 USD
    assert currency.from_eur(1.0, "USD") == pytest.approx(
        1.08, rel=1e-6
    )


def test_build_price_fields_includes_eur_and_usd():
    fields = currency.build_price_fields(100, "EUR")

    assert fields["EUR"] == pytest.approx(100.0)
    assert fields["USD"] == pytest.approx(108.0)


def test_build_price_fields_from_bgn():
    fields = currency.build_price_fields(1.95583, "BGN")

    assert fields["EUR"] == pytest.approx(1.0)
    assert fields["USD"] == pytest.approx(1.08, rel=1e-4)


# ============================================================
# TAG: NULL / ERROR HANDLING
# ============================================================

def test_none_stays_none():
    assert currency.to_eur(None, "BGN") is None
    assert currency.from_eur(None, "USD") is None
    assert currency.convert(None, "BGN", "USD") is None


def test_non_numeric_stays_none():
    assert currency.to_eur("не е число", "BGN") is None


def test_unknown_currency_raises_with_hint():
    with pytest.raises(KeyError) as info:
        currency.to_eur(100, "XYZ")

    assert "register_currency" in str(info.value)


# ============================================================
# TAG: EXTENSIBILITY
# ============================================================

def test_register_currency_adds_new_code():
    # 1 EUR = 0.85 GBP, следователно 0.85 GBP = 1 EUR.
    currency.register_currency("GBP", 0.85, "British Pound")

    try:
        assert currency.is_supported("GBP")
        assert currency.to_eur(0.85, "GBP") == pytest.approx(1.0)
    finally:
        currency._STATIC_RATES.pop("GBP", None)
        currency._CURRENCY_NAMES.pop("GBP", None)


def test_register_currency_refuses_to_silently_overwrite():
    with pytest.raises(ValueError):
        currency.register_currency("USD", 2.0, "duplicate")


def test_register_currency_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        currency.register_currency("ZZZ", 0, "zero")


# ============================================================
# TAG: SOURCE CURRENCY REGISTRY
# ============================================================

def test_sofiaplan_declared_in_bgn():
    assert currency.source_currency("sofiaplan") == "BGN"


def test_unknown_source_defaults_to_eur():
    assert currency.source_currency("непознат") == "EUR"
    assert currency.source_currency(None) == "EUR"


def test_register_source_currency():
    currency.register_source_currency("testportal", "USD")

    try:
        assert currency.source_currency("testportal") == "USD"
    finally:
        currency._SOURCE_CURRENCY.pop("testportal", None)


def test_register_source_currency_rejects_unknown_code():
    with pytest.raises(KeyError):
        currency.register_source_currency("x", "XYZ")


# ============================================================
# TAG: EXPORT FIELDS
# ============================================================

def test_export_currencies_always_includes_base():
    codes = currency.export_currencies()

    assert codes[0] == "EUR"
    assert "USD" in codes


def test_describe_reports_base_flag():
    described = currency.describe()

    assert described["EUR"]["is_base"] is True
    assert described["USD"]["is_base"] is False
    assert described["BGN"]["per_eur"] == pytest.approx(1.95583)
