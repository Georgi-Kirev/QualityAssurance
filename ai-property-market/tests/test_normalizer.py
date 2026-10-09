"""
Тестове за tools/normalizer.py

Покрива normalize_record, detect_area, detect_price и
normalize_area. Тук са регрессионните тестове за бъговете,
които бяха открити:

  * "are" substring -> area_kv_m се четеше като "ar" (x100)
  * "ha" substring -> shape/share/phases -> x10000
  * "da" трябва да е преди m2, но СЛЕД hectare
"""

import pytest


# ============================================================
# TAG: normalize_area - ЕДИНИЦИ
# ============================================================

@pytest.mark.parametrize(
    "field,value,expected_unit,expected_m2",
    [
        ("area_kv_m", 3610.93, "m2", 3610.93),
        ("area_kvm", 3610.93, "m2", 3610.93),
        ("area_kvadratni_m", 3610.93, "m2", 3610.93),
        ("plosht_m2", 3610.93, "m2", 3610.93),
        ("area_m2", 80.0, "m2", 80.0),
        ("area_sqm", 80.0, "m2", 80.0),
        ("area_ha", 2.0, "ha", 20000.0),
        ("area_hectares", 2.0, "ha", 20000.0),
        ("area_dka", 5.0, "dka", 5000.0),
        ("area_decare", 5.0, "dka", 5000.0),
        ("area_ar", 50.0, "ar", 5000.0),
        ("area_ares", 50.0, "ar", 5000.0),
        ("da", 7.0, "dka", 7000.0),
    ]
)
def test_normalize_area_known_units(
    normalizer,
    field,
    value,
    expected_unit,
    expected_m2
):
    result = normalizer.normalize_area(field, value)

    assert result is not None, f"{field} не даде площ"
    assert result["unit"] == expected_unit
    assert result["square_meters"] == pytest.approx(
        expected_m2
    )
    assert result["source_field"] == field


@pytest.mark.parametrize(
    "field",
    [
        # "are" като substring -> вече не трябва да е ar
        "area_share",
        "share_area",
        # "ha" като substring -> вече не трябва да е ha
        "shape",
        "share",
        "phases",
        "characteristic",
        "has_area",
        # без единица
        "area",
        "plosht",
        "surface",
        "count",
        "id",
    ]
)
def test_normalize_area_rejects_non_area_fields(
    normalizer,
    field
):
    result = normalizer.normalize_area(field, 12345.0)

    assert result is None, (
        f"{field!r} не бива да се разпознава като площ, "
        f"получено: {result}"
    )


def test_normalize_area_hectare_beats_are(normalizer):
    """
    Редът на проверките: hectare трябва да се провери преди "ar",
    защото "are" е substring на "hectare"... и обратно -
    "area_ha" не бива да се прочете като ar.
    """

    assert normalizer.normalize_area(
        "area_ha", 1.0
    )["square_meters"] == 10000.0


def test_normalize_area_kvm_is_not_ar(normalizer):
    """
    РЕГРЕСИЯ: area_kv_m -> "ar" -> 361093 вместо 3610.93
    """

    result = normalizer.normalize_area(
        "area_kv_m", 3610.93
    )

    assert result["unit"] == "m2"
    assert result["square_meters"] == pytest.approx(3610.93)
    assert result["square_meters"] < 10000.0


@pytest.mark.parametrize(
    "value",
    [None, "", "abc", -5, 0, float("nan"), float("inf")]
)
def test_normalize_area_invalid_values(normalizer, value):
    result = normalizer.normalize_area(
        "area_m2", value
    )

    assert result is None


# ============================================================
# TAG: detect_price
# ============================================================

def test_detect_price_eur(normalizer):
    result = normalizer.detect_price(
        {"cena_ap": 116813.079}
    )

    assert result is not None
    assert result["currency"] == "EUR"
    assert result["value_eur"] == pytest.approx(116813.08)
    assert result["source_field"] == "cena_ap"
    assert isinstance(result["value_eur"], float), (
        "value_eur ТРЯБВА да е float - ако е string, "
        "extract_price() по-късно връща None"
    )


def test_detect_price_bgn_converted(normalizer):
    result = normalizer.detect_price(
        {"price_bgn": 195583.0}
    )

    assert result is not None

    # "currency" вече е КАНОНИЧНОТО представяне (винаги EUR),
    # а естествената валута на източника се пази отделно.
    # Старият контракт казваше currency="BGN" дори при
    # value_eur=100000, което е само по себе си противоречие.
    assert result["currency"] == "EUR"
    assert result["source_currency"] == "BGN"
    assert result["source_value"] == pytest.approx(195583.0)
    assert result["value_eur"] == pytest.approx(
        100000.0, rel=1e-4
    )


def test_detect_price_converts_unsuffixed_bgn_from_source(normalizer):
    """
    РЕГРЕСИОНЕН ТЕСТ.

    Бъг: detect_price() приемаше всяко поле без валутен суфикс
    за EUR. SofiaPlan публикува в ЛЕВА, а полето се казва
    "cena_ap" (cena = цена, без суфикс). Резултатът беше
    {"value_eur": 116813.08, "currency": "EUR"} за стойност,
    която всъщност е 116 813 BGN = ~59 725 EUR.

    Грешката беше ~2x във всеки ценови запис в продукта.
    """
    result = normalizer.detect_price(
        {"cena_ap": 116813.079},
        source="sofiaplan",
    )

    assert result is not None
    assert result["source_currency"] == "BGN"
    assert result["raw_value"] == pytest.approx(116813.079)
    assert result["value_eur"] == pytest.approx(
        59725.58, rel=1e-4
    )
    assert result["value_eur"] < result["raw_value"] / 1.9, (
        "цената в EUR трябва да е ~2x по-малка от суровата стойност в лева"
    )


def test_detect_price_without_source_keeps_eur_assumption(normalizer):
    """
    Без източник се запазва EUR - това е старият, но документиран
    поведенчески контракт за източници, които не са в EUR.
    """
    result = normalizer.detect_price({"cena_ap": 100000.0})

    assert result is not None
    assert result["value_eur"] == pytest.approx(100000.0)
    assert "source_currency" not in result


def test_detect_price_bulgarian_field_name(normalizer):
    result = normalizer.detect_price(
        {"цена": 100000.0}
    )

    assert result is not None
    assert result["value_eur"] == pytest.approx(100000.0)


def test_detect_price_string_input(normalizer):
    result = normalizer.detect_price(
        {"price": "116 813,08 EUR"}
    )

    assert result is not None
    assert result["value_eur"] == pytest.approx(116813.08)


def test_detect_price_returns_float_not_string(normalizer):
    """
    РЕГРЕСИЯ: ijson връща Decimal, json.dumps(default=str)
    го превръща в string, extract_price() после връща None.
    Тук гарантираме, че самият normalizer пише float.
    """

    result = normalizer.detect_price(
        {"cena_ap": 116813.079}
    )

    assert not isinstance(result["value_eur"], str)
    assert not isinstance(result["raw_value"], str)


def test_detect_price_no_match(normalizer):
    assert normalizer.detect_price(
        {"length_m": 19.09, "name_": "Искър"}
    ) is None


def test_detect_price_rejects_non_positive(normalizer):
    assert normalizer.detect_price({"price": 0}) is None
    assert normalizer.detect_price({"price": -5}) is None


def test_detect_price_not_a_dict(normalizer):
    assert normalizer.detect_price(None) is None
    assert normalizer.detect_price([1, 2]) is None


# ============================================================
# TAG: normalize_record
# ============================================================

def test_normalize_record_structure(normalizer, sample_features):
    record = normalizer.normalize_record(
        sample_features[0],
        "sofiaplan",
        999
    )

    assert record["source"] == "sofiaplan"
    assert record["dataset_id"] == 999
    assert "attributes" in record
    assert "location" in record

    assert record["attributes"]["regname"] == "ЦГЧ Зона А - север"
    assert record["area"]["unit"] == "m2"
    assert record["area"]["square_meters"] == pytest.approx(98225.38)

    # SofiaPlan е в ЛЕВА. Стойността 116 813.079 в суровните
    # данни е BGN, т.е. ~59 725 EUR. Старият тест тук
    # очакваше 116813.08 - това беше точно бъгът, който
    # удвояваше всички цени в продукта.
    price = record["price"]
    assert price["source_currency"] == "BGN"
    assert price["raw_value"] == pytest.approx(116813.079)
    assert price["value_eur"] == pytest.approx(59725.58, rel=1e-4)


def test_normalize_record_extracts_geometry(normalizer, sample_features):
    record = normalizer.normalize_record(
        sample_features[0],
        "sofiaplan",
        999
    )

    geometry = record["location"]["geometry"]

    assert geometry["type"] == "Polygon"


def test_normalize_record_without_price_field(normalizer, sample_features):
    record = normalizer.normalize_record(
        sample_features[3],
        "sofiaplan",
        220
    )

    assert "cena_ap" not in record["attributes"]
    assert record.get("price") is None


def test_normalize_record_area_only_record(normalizer, sample_features):
    """
    Запис 6 (green-1) има area_kv_m, но няма цена.
    """

    record = normalizer.normalize_record(
        sample_features[5],
        "sofiaplan",
        368
    )

    assert record["area"]["unit"] == "m2"
    assert record["area"]["square_meters"] == pytest.approx(3610.93)
    assert record.get("price") is None


# ============================================================
# TAG: detect_area
# ============================================================

def test_detect_area_prefers_area_field(normalizer):
    result = normalizer.detect_area(
        {
            "area_kv_m": 3610.93,
            "length_m": 19.09
        }
    )

    assert result is not None
    assert result["source_field"] == "area_kv_m"


def test_detect_area_none_when_nothing(normalizer):
    result = normalizer.detect_area(
        {
            "length_m": 19.09,
            "kanal": 0.0
        }
    )

    assert result is None


def test_detect_area_not_a_dict(normalizer):
    assert normalizer.detect_area(None) is None
    assert normalizer.detect_area("x") is None


# ============================================================
# TAG: detect_json_structure
# ============================================================

def test_detect_geojson_structure(normalizer, tmp_path):
    path = tmp_path / "raw.geojson"

    path.write_text(
        '{"type":"FeatureCollection","features":['
        '{"type":"Feature","properties":{"a":1},'
        '"geometry":{"type":"Point","coordinates":[23.3,42.7]}}]}',
        encoding="utf-8"
    )

    assert normalizer.detect_json_structure(path) == "features.item"


def test_detect_root_array_structure(normalizer, tmp_path):
    path = tmp_path / "raw.json"
    path.write_text('[{"a":1},{"a":2}]', encoding="utf-8")

    assert normalizer.detect_json_structure(path) == "item"


@pytest.mark.parametrize(
    "wrapper", ["records", "data", "items", "results"]
)
def test_detect_wrapper_structures(normalizer, tmp_path, wrapper):
    path = tmp_path / "raw.json"
    path.write_text(
        '{"%s":[{"a":1}]}' % wrapper,
        encoding="utf-8"
    )

    assert normalizer.detect_json_structure(path) == f"{wrapper}.item"


# ============================================================
# TAG: iter_json_records - NUMERIC INTEGRITY
# ============================================================

def test_iter_json_records_preserves_numbers(normalizer, tmp_path):
    """
    РЕГРЕСИЯ: ijson без use_float=True връща Decimal.
    Всички числа трябва да са int/float, НЕ Decimal и
    със сигурност НЕ string.
    """

    from decimal import Decimal

    path = tmp_path / "raw.json"
    path.write_text(
        '[{"cena_ap":116813.079,"area_kv_m":98225.38,'
        '"godina":2019,"id":"abc"}]',
        encoding="utf-8"
    )

    records = list(normalizer.iter_json_records(path))

    assert len(records) == 1

    record = records[0]

    assert isinstance(record["cena_ap"], float)
    assert not isinstance(record["cena_ap"], Decimal)
    assert not isinstance(record["cena_ap"], str)

    assert isinstance(record["godina"], int)
    assert not isinstance(record["godina"], str)

    assert record["cena_ap"] == pytest.approx(116813.079)

# ============================================================
# TAG: CANCEL / STALE METADATA
# ============================================================

def test_cancelled_run_writes_no_metadata(
    normalizer,
    mini_raw_snapshot,
    isolated_storage,
    monkeypatch,
):
    """
    При отказ metadata.json НЕ се записва.

    run_timestamp е по час, затова прекъснат run споделя
    папка с предишен завършен run. Ако metadata.json
    остане, прекъснатият run ще изглежда завършен за
    dedup / consolidator.
    """

    import threading

    monkeypatch.setattr(
        normalizer,
        "RAW_DIR",
        mini_raw_snapshot.parents[1],
    )

    cancel = threading.Event()
    cancel.set()

    rc = normalizer.main(cancel_event=cancel)

    assert rc == 130

    newest = max(
        normalizer.NORMALIZED_DIR.iterdir(),
        key=lambda p: p.stat().st_mtime,
    )

    assert not (newest / "metadata.json").exists()


def test_stale_metadata_is_replaced_on_success(
    normalizer,
    mini_raw_snapshot,
    isolated_storage,
    monkeypatch,
):
    """
    Стар metadata.json се изтрива В НАЧАЛОТО на run-а,
    а не накрая - иначе прекъсването оставя фалшив маркер.
    """

    monkeypatch.setattr(
        normalizer,
        "RAW_DIR",
        mini_raw_snapshot.parents[1],
    )

    run_dir = (
        normalizer.NORMALIZED_DIR
        / normalizer.current_timestamp()
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    stale = run_dir / "metadata.json"
    stale.write_text(
        '{"stale": true}',
        encoding="utf-8"
    )

    normalizer.main(cancel_event=None)

    assert stale.exists()

    assert '"stale"' not in stale.read_text(
        encoding="utf-8"
    )
