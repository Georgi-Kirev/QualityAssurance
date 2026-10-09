"""
Тестове за tools/matcher.py

Тук са регрессионните тестове за трите критични бъга:

  1. extract_address() приемаше "location" (геометрия) за
     адрес -> 25 825 фалшиви MATCH-а.
  2. score = weighted/available*100 -> само координати = 100%
  3. Нямаше извличане на координати от GeoJSON геометрия,
     така че blocking-ът по координати не работеше.
  4. detect_area_unit_from_field() нямаше kvm алиаси.
"""

import pytest


# ============================================================
# TAG: extract_address - РЕГРЕСИЯ 1
# ============================================================

def test_location_is_not_an_address(matcher):
    """
    РЕГРЕСИЯ: record["location"] е контейнер за геометрия.
    Ако се приеме за адрес, две записи със СЪЩАТА геометрия
    получават address match при score 100.
    """

    record = {
        "location": {
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[23.6, 42.4], [23.6, 42.5]]]
            }
        }
    }

    assert matcher.extract_address(record) == ""


def test_top_level_geometry_is_not_an_address(matcher):
    record = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[23.1, 42.1], [23.2, 42.2]]]
        }
    }

    assert matcher.extract_address(record) == ""


def test_real_address_is_extracted(matcher):
    record = {
        "address": "булевард България 12"
    }

    result = matcher.extract_address(record)

    assert result
    assert "българия" in result


@pytest.mark.parametrize(
    "value",
    [
        None, "", "   ", "N/A", "n/a", "NA", "none", "None",
        "null", "NULL", "nil", "undefined", "unknown",
        "-", "--", "?", "no address", "без адрес",
        "няма", "нема", "липсва", "неизвестен"
    ]
)
def test_placeholder_addresses_are_rejected(matcher, value):
    """
    Всички записи с placeholder адрес трябва да дадат
    празен адрес, иначе те се матчват помежду си на 100.
    """

    assert matcher.extract_address(
        {"address": value}
    ) == ""


def test_weak_short_address_rejected(matcher):
    """
    "5" / "N" / "7А" са твърде слаби сигнали за адрес.
    ("ул. 5" отгоре остава приемлив адрес, но сам по себе
    си address = 20 точки < MIN_WEIGHT_FOR_MATCH, така че
    не може да произведе MATCH - виж теста по-долу.)
    """

    for value in ("5", "N", "7A", "1"):
        assert matcher.extract_address(
            {"address": value}
        ) == "", f"{value!r} не бива да е адрес"


def test_non_scalar_address_rejected(matcher):
    assert matcher.extract_address(
        {"address": {"nested": "value"}}
    ) == ""
    assert matcher.extract_address(
        {"address": ["a", "b"]}
    ) == ""


def test_boolean_address_rejected(matcher):
    assert matcher.extract_address(
        {"address": True}
    ) == ""


def test_numeric_address_rejected(matcher):
    """
    Число не е адрес. Иначе записи с object_id=1 и
    object_id=1 стават MATCH.
    """

    assert matcher.extract_address(
        {"address": 12345}
    ) == ""


# ============================================================
# TAG: COORDINATES FROM GEOJSON - РЕГРЕСИЯ 3
# ============================================================

@pytest.mark.parametrize(
    "geometry,expected",
    [
        (
            {"type": "Point", "coordinates": [23.3219, 42.6977]},
            (42.6977, 23.3219)
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[23.1, 42.1], [23.2, 42.2]]]
            },
            (42.1, 23.1)
        ),
        (
            {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[23.46, 42.78], [23.47, 42.79]]],
                    [[[23.50, 42.80], [23.51, 42.81]]]
                ]
            },
            (42.78, 23.46)
        ),
        (
            {
                "type": "LineString",
                "coordinates": [[23.1, 42.1], [23.2, 42.2]]
            },
            (42.1, 23.1)
        ),
        (
            {
                "type": "MultiLineString",
                "coordinates": [
                    [[23.61, 42.42], [23.61, 42.43]],
                    [[23.63, 42.44], [23.63, 42.45]]
                ]
            },
            (42.42, 23.61)
        ),
    ]
)
def test_coordinates_from_geometry(matcher, geometry, expected):
    """
    GeoJSON е [lon, lat] - редът е ЗАДЪЛЖИТЕЛЕН.
    """

    record = {"location": {"geometry": geometry}}

    assert matcher.extract_coordinates(record) == expected


def test_lat_lon_fields_take_precedence(matcher):
    record = {
        "lat": 42.6977,
        "lon": 23.3219
    }

    assert matcher.extract_coordinates(record) == (42.6977, 23.3219)


def test_no_coordinates_returns_none(matcher):
    assert matcher.extract_coordinates({}) is None
    assert matcher.extract_coordinates(
        {"location": {}}
    ) is None
    assert matcher.extract_coordinates(
        {"location": {"geometry": {"type": "Point", "coordinates": []}}}
    ) is None


def test_out_of_range_geometry_rejected(matcher):
    record = {
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [999, 999]
            }
        }
    }

    assert matcher.extract_coordinates(record) is None


# ============================================================
# TAG: AREA UNITS - РЕГРЕСИЯ 4
# ============================================================

@pytest.mark.parametrize(
    "field,expected",
    [
        ("area_kv_m", "m2"),
        ("area_kvm", "m2"),
        ("area_kvadratni_m", "m2"),
        ("area_m2", "m2"),
        ("area_sqm", "m2"),
        ("surface_m2", "m2"),
        ("area_ha", "ha"),
        ("area_hectares", "ha"),
        ("area_dka", "dka"),
        ("area_ar", "ar"),
        ("area_ares", "ar"),
        ("area_m", "m2"),
        # не бива да се разпознават
        ("area", None),
        ("shape", None),
        ("share", None),
        ("has_area", None),
        ("plosht", None),
    ]
)
def test_detect_area_unit(matcher, field, expected):
    assert matcher.detect_area_unit_from_field(field) == expected


def test_extract_area_m2_bulgarian_field(matcher):
    """
    РЕГРЕСИЯ: area_kv_m връщаше None - matcher-ът
    напълно игнорираше единственото реално area поле.
    """

    assert matcher.extract_area_m2(
        {"area_kv_m": 3610.93}
    ) == pytest.approx(3610.93)


def test_extract_area_m2_unit_conversion(matcher):
    assert matcher.extract_area_m2(
        {"area_ha": 2.0}
    ) == pytest.approx(20000.0)
    assert matcher.extract_area_m2(
        {"area_dka": 5.0}
    ) == pytest.approx(5000.0)
    assert matcher.extract_area_m2(
        {"area_ar": 50.0}
    ) == pytest.approx(5000.0)


def test_extract_area_m2_ignores_non_area(matcher):
    assert matcher.extract_area_m2(
        {"length_m": 19.09, "kanal": 0.0}
    ) is None


def test_extract_area_m2_area_with_unit_field(matcher):
    assert matcher.extract_area_m2(
        {"area": 80, "area_unit": "m2"}
    ) == pytest.approx(80)


# ============================================================
# TAG: SCORING - РЕГРЕСИЯ 2
# ============================================================

def _features(record):
    from tools.matcher import extract_features
    return extract_features(record)


def test_coordinates_only_is_never_match(matcher):
    """
    РЕГРЕСИЯ: само координати (15 точки) даваше
    15/15 = 100% -> MATCH. Сега трябва да е UNMATCHED.
    """

    a = _features({
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    b = _features({
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    result = matcher.compare_features(a, b)

    assert result["status"] == "UNMATCHED", (
        "само координати не е достатъчно за MATCH"
    )


def test_coordinates_plus_area_is_never_match(matcher):
    """
    coordinates (15) + area_m2 (10) = 25 < 30
    """

    a = _features({
        "area_m2": 5000.0,
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    b = _features({
        "area_m2": 5000.0,
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    result = matcher.compare_features(a, b)

    assert result["status"] == "UNMATCHED"


def test_address_alone_is_never_match(matcher):
    """
    address = 20 точки < MIN_WEIGHT_FOR_MATCH (30).
    Съвпадащ адрес без друго потвърждение е UNMATCHED.
    """

    address = "ul. Vasil Levski 100, Sofia"

    result = matcher.compare_features(
        _features({"address": address}),
        _features({"address": address})
    )

    assert result["status"] == "UNMATCHED", (
        "само адрес не е достатъчно за MATCH"
    )


def test_address_plus_coordinates_is_match(matcher):
    """
    address (20) + coordinates (15) = 35 >= 30 -> MATCH
    """

    address = "ul. Vasil Levski 100, Sofia"

    a = _features({
        "address": address,
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    b = _features({
        "address": address,
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3, 42.7]
            }
        }
    })

    result = matcher.compare_features(a, b)

    assert result["status"] == "MATCH"
    assert "address_exact_or_normalized" in result["signals"]


def test_global_identifier_alone_is_match(matcher):
    a = _features({"cadastral_id": "1234/5678"})
    b = _features({"cadastral_id": "1234/5678"})

    result = matcher.compare_features(a, b)

    assert result["status"] == "MATCH"


def test_different_address_is_hard_conflict(matcher):
    a = _features({"address": "ul. Vasil Levski 100, Sofia"})
    b = _features({"address": "ul. Ivan Vazov 55, Plovdiv"})

    result = matcher.compare_features(a, b)

    assert result["status"] != "MATCH"
    assert "address_different" in result["conflicts"]


def test_empty_features_unmatched(matcher):
    a = _features({})
    b = _features({})

    result = matcher.compare_features(a, b)

    assert result["status"] == "UNMATCHED"


# ============================================================
# TAG: BLOCKING KEYS
# ============================================================

def test_blocking_keys_include_geo(matcher):
    features = _features({
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3219, 42.6977]
            }
        }
    })

    keys = matcher.make_blocking_keys(features)

    assert any(
        key.startswith("geo:")
        for key in keys
    ), f"липсва geo blocking ключ: {keys}"


def test_blocking_keys_skip_empty_address(matcher):
    features = _features({
        "location": {
            "geometry": {
                "type": "Point",
                "coordinates": [23.3219, 42.6977]
            }
        }
    })

    keys = matcher.make_blocking_keys(features)

    assert not any(
        key.startswith("address:")
        for key in keys
    ), f"празен адрес не трябва да е blocking ключ: {keys}"


def test_blocking_keys_include_global_id(matcher):
    features = _features({"cadastral_id": "1234/5678"})

    keys = matcher.make_blocking_keys(features)

    assert any(
        key.startswith("gid:")
        for key in keys
    )


def test_no_blocking_keys_for_empty_record(matcher):
    features = _features({})

    assert matcher.make_blocking_keys(features) == []


# ============================================================
# TAG: GLOBAL IDENTIFIERS
# ============================================================

def test_object_id_is_not_a_global_id(matcher):
    """
    object_id/id са SOURCE id-та - не бива да се ползват
    за entity matching, защото се повтарят в различни слоеве.
    """

    result = matcher.extract_global_identifiers({
        "object_id": 1,
        "id": "abc-123"
    })

    assert result == []


def test_cadastral_id_is_a_global_id(matcher):
    result = matcher.extract_global_identifiers({
        "cadastral_id": "1234/5678"
    })

    assert "1234/5678" in result


# ============================================================
# TAG: is_area_field
# ============================================================

@pytest.mark.parametrize(
    "field,expected",
    [
        ("area_m2", True),
        ("area_kv_m", True),
        ("surface", True),
        ("plosht", True),
        ("sqm", True),
        ("kvm", True),
        ("length_m", False),
        ("kanal", False),
        ("godina", False),
        ("name_", False),
    ]
)
def test_is_area_field(matcher, field, expected):
    assert matcher.is_area_field(field) is expected
