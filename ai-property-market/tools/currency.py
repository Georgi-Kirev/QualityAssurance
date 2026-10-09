# TAG: CURRENCY
# Калкулатор за валути. Базова валута = EUR.
#
# ЗАЩО СЪЩЕСТВУВА:
#   SofiaPlan (и повечето български open-data източници) публикуват
#   цените в ЛЕВА. Нормализаторът ги записваше в блок
#   {"value_eur": X, "currency": "EUR"} БЕЗ конверсия, т.е.
#   число 116 813 беше етикетирано "EUR", а всъщност е 116 813 BGN
#   (~59 700 EUR). Това е ~2x грешка в целия продукт.
#
# ЗАЩО НЕ ИМА МРЕЖА:
#   Pipeline-ът трябва да е бърз, офлайн и детерминиран. Затова
#   курсовете са статични таблици. Ако трябва живи курсове, виж
#   `set_rates()` / `load_rates_from()` по-долу - те са отделени
#   от ядрото, за да не правят мрежова заявка при import.
#
# РАЗШИРЯВАНЕ:
#   Добави нова валута с `register_currency("GBP", 0.85, "British Pound")`
#   и тя веднага участва в to_eur()/from_eur()/convert() и в
#   изхода на finished_properties.json.

from typing import Dict, Optional


# TAG: BASE CURRENCY

BASE_CURRENCY = "EUR"


# TAG: STATIC RATES
#
# Стойността е "колко единици от тази валута са 1 EUR".
# EUR  = 1.0            (база)
# BGN  = 1.95583        фиксиран курс, законно свързан с EUR (ERM II)
# USD  = 1.08           приблизителен; НЕ е жива котировка
#
# ЗАБЕЛЕЖКА ЗА BGN: 1 EUR = 1.95583 BGN е ФИКСИРАН от Българска
# народна банка от 1999 г. и не се променя. Това е единственият курс
# тук, който може да се смята за точен.
#
# ЗАБЕЛЕЖКА ЗА USD: плаващ курс. Ако продаваш данни на клиенти,
# смятай цената в EUR (стабилно), а USD давай само като
# информативно допълнение. Не таксувай в USD.

_STATIC_RATES: Dict[str, float] = {
    "EUR": 1.0,
    "BGN": 1.95583,
    "USD": 1.08,
}

_CURRENCY_NAMES: Dict[str, str] = {
    "EUR": "Euro",
    "BGN": "Bulgarian Lev",
    "USD": "US Dollar",
}

# Валутите, които излизат в finished_properties.json.
# EUR винаги е първа и е основната.
DEFAULT_EXPORT_CURRENCIES = ("EUR", "USD")


# TAG: REGISTRY ACCESS

def _rates() -> Dict[str, float]:
    return _STATIC_RATES


def supported_currencies() -> list:
    """Всички поддържани валутни кодове."""
    return sorted(_rates().keys())


def currency_name(code: str) -> str:
    return _CURRENCY_NAMES.get(_normalize(code), code.upper())


def is_supported(code: str) -> bool:
    return _normalize(code) in _rates()


def export_currencies() -> list:
    """
    Валутите, които трябва да се запишат в изходния файл.

    Винаги включва BASE_CURRENCY, после останалите в DEFAULT_EXPORT.
    """
    ordered = [BASE_CURRENCY]
    for code in DEFAULT_EXPORT_CURRENCIES:
        norm = _normalize(code)
        if norm != BASE_CURRENCY and norm not in ordered:
            ordered.append(norm)
    return ordered


# TAG: SOURCE CURRENCY REGISTRY
#
# В коя валута публикува ВСЕКИ ИЗТОЧНИК. Това е различен въпрос от
# "в коя валута искаме да продаваме" - тук говорим за естествената
# валута на суровите данни.
#
# ВАЖНО: Без тази таблица една цена без изричен суфикс (cena_ap,
# price, st_price) би се приела за EUR. За български източник това
# означава ~2x грешка нагоре. Затова неизвестното се третира
# като EUR САМО ако източникът наистина е в EUR.
#
# Добавяне на източник:
#     register_source_currency("imot.bg", "BGN")

_SOURCE_CURRENCY: Dict[str, str] = {
    "sofiaplan": "BGN",
}

DEFAULT_SOURCE_CURRENCY = "EUR"


def register_source_currency(source: str, code: str) -> None:
    """Декларира в коя валута публикува даден източник."""
    norm_source = str(source).strip().lower()
    norm_code = _normalize(code)
    if norm_code not in _STATIC_RATES:
        raise KeyError(
            f"Cannot register {norm_source}: unknown currency {norm_code!r}"
        )
    _SOURCE_CURRENCY[norm_source] = norm_code


def source_currency(source: Optional[str]) -> str:
    """
    Валутата на даден източник.

    Непознат източник -> DEFAULT_SOURCE_CURRENCY (EUR).
    """
    if not source:
        return DEFAULT_SOURCE_CURRENCY
    return _SOURCE_CURRENCY.get(
        str(source).strip().lower(),
        DEFAULT_SOURCE_CURRENCY,
    )


def describe_sources() -> Dict[str, str]:
    return {
        source: code
        for source, code in sorted(_SOURCE_CURRENCY.items())
    }


# TAG: NORMALIZATION

def _normalize(code: Optional[str]) -> str:
    if code is None:
        return BASE_CURRENCY
    text = str(code).strip().upper()
    if not text:
        return BASE_CURRENCY
    return text


# TAG: RATE OVERRIDE

def set_rates(rates: Dict[str, float]) -> None:
    """
    Заменя статичните курсове (напр. с живи от API).
    Стойността трябва да е "единици валута за 1 EUR".
    """
    for code, rate in rates.items():
        norm = _normalize(code)
        try:
            value = float(rate)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid rate for {code}: {rate!r}")
        if value <= 0:
            raise ValueError(f"Rate for {code} must be > 0, got {value}")
        _STATIC_RATES[norm] = value


def register_currency(code: str, per_eur: float, name: str = "") -> None:
    """
    Добавя нова валута.

    per_eur: колко единици от новата валута са 1 EUR.
             GBP е по-силен от EUR, 1 EUR ~ 0.85 GBP, така че
             правилното обаждане е register_currency("GBP", 0.85).
             Обърни внимание: НЕ 1/0.85 - това би дало обратното
             значение и грешка от ~1.9 пъти.
    """
    norm = _normalize(code)
    if norm in _STATIC_RATES and norm != BASE_CURRENCY:
        # Не презаписваме вече дефинирана валута мълчаливо -
        # това почти винаги е грешка.
        raise ValueError(f"Currency {norm} already registered")
    try:
        value = float(per_eur)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid rate for {code}: {per_eur!r}")
    if value <= 0:
        raise ValueError(f"Rate for {code} must be > 0, got {value}")
    _STATIC_RATES[norm] = value
    _CURRENCY_NAMES[norm] = name or norm


# TAG: CONVERSION

def _rate_to_eur(code: str) -> float:
    rates = _rates()
    norm = _normalize(code)
    if norm not in rates:
        raise KeyError(
            f"Unsupported currency {norm!r}. "
            f"Supported: {', '.join(supported_currencies())}. "
            f"Добави я с register_currency()."
        )
    return rates[norm]


def to_eur(value, from_currency: str = BASE_CURRENCY):
    """
    Преобразува стойност в EUR.

    value=None -> None (без стойност не се конвертира)
    """
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    norm = _normalize(from_currency)
    if norm == BASE_CURRENCY:
        return amount
    return amount / _rate_to_eur(norm)


def from_eur(value, to_currency: str = BASE_CURRENCY):
    """
    Преобразува стойност от EUR в друга валута.
    """
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    norm = _normalize(to_currency)
    if norm == BASE_CURRENCY:
        return amount
    return amount * _rate_to_eur(norm)


def convert(value, from_currency: str, to_currency: str):
    """
    Преобразува между две произволни валути.
    """
    if value is None:
        return None
    amount_in_eur = to_eur(value, from_currency)
    if amount_in_eur is None:
        return None
    return from_eur(amount_in_eur, to_currency)


def build_price_fields(
    value,
    from_currency: str = BASE_CURRENCY,
    currencies=None,
) -> Dict[str, Optional[float]]:
    """
    Прави готовия "price_block" за finished_properties.json.

    Връща {"EUR": ..., "USD": ...} - всички стойности са float,
    подредени в реалната точност (2 знака за пари).

    Примери:
        build_price_fields(116813.08, "BGN")
        -> {"EUR": 59719.87, "USD": 64497.46}

        build_price_fields(100, "EUR")
        -> {"EUR": 100.0, "USD": 108.0}
    """
    targets = list(currencies) if currencies else export_currencies()
    out: Dict[str, Optional[float]] = {}
    for code in targets:
        norm = _normalize(code)
        converted = convert(value, from_currency, norm)
        out[norm] = round(converted, 2) if converted is not None else None
    return out


def describe() -> Dict[str, Dict[str, object]]:
    """
    Описание на поддържаните валути - за /api/stats и за UI.
    """
    rates = _rates()
    return {
        code: {
            "name": _CURRENCY_NAMES.get(code, code),
            "per_eur": round(rates[code], 5),
            "is_base": code == BASE_CURRENCY,
        }
        for code in sorted(rates.keys())
    }
