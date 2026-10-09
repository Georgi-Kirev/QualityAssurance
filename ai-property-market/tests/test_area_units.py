# TAG: TESTS - AREA UNITS AND AREA PLAUSIBILITY
#
# Два отделни проблема, които приличат, но нямат нищо
# общо. Важно е да не се смесват.
#
#
# --- ПРОБЛЕМ 1: area_km2 се четеше като m2 ------------------
#
# ГРЕШКА x1 000 000. Проверено на реални данни:
# dataset 604, 564 записа, всички с площ от тип area_km2.
#
#   "area_km2" ЗАВЪРШВА на "m2". Сталият цикъл в
#   detect_area_unit_from_field() връщаше "m2" -> множител
#   1.0. Същото и в normalize_area(): клонът за m2 хващаше
#   името, защото съдържа "m2".
#
#   2.4 km2 -> 2.4 m2
#
# Защо никой не го е забелязал: 2.4 е напълно правдоподобна
# стойност и в метри. Грешката е видима САМО при мащаба -
# точно затова тестовете по-долу проверяват множителя, а не
# стойността.
#
#
# --- ПРОБЛЕМ 2: средната площ 522 148 m2 ------------------
#
# Това НЕ е бъг в превода. Пресметнато е вярно за това, което
# е измерено, и безсмислено като "средна площ на имот".
#
# Разпределението на 297 170 записа с площ, snapshot
# 28-09-2026_19, измерено на 28.09.2026:
#
#   p1  =          7.8 m2
#   p25 =        911.1 m2
#   p50 =      9 499.7 m2   <-- медианата
#   p75 =    264 522.8 m2
#   p90 =    999 912.2 m2   <-- ~1 km2
#   p99 =  6 001 423.0 m2   <-- ~6 km2
#
# Медиана 9 500 m2 и кръглите стойности около 1 и 6 km2
# казват какво е това: векторни ЗОНИ - райони и квартали на
# София. Люлин ~3.6 km2, Слатина ~8.5 km2, Витоша ~59 km2.
# Числата съвпадат.
#
# ЗАТОВО НЕ ФИЛТРИРАМ ЗАПИСИТЕ. Филтърът би дал по-красиво
# средно и би скрил точно факта, че продуктът няма имотни
# обяви. Вместо това площите се БРОЯТ и ПУБЛИКУВАТ, без да
# се променя нито един запис.
#
# ИЗМЕРЕНО Е, НЕ Е ДОПУСКАНО. И втората част на файла пази
# точно това решение.

import pytest

from tools import normalizer
from tools.finished_exporter import (
    PROPERTY_AREA_MAX_M2,
    PROPERTY_AREA_MIN_M2,
    describe_area_plausibility,
    extract_area,
    merge_area_plausibility,
    new_area_plausibility,
    observe_area,
    split_area,
)
from tools.matcher import (
    area_to_m2,
    detect_area_unit_from_field,
)


# ============================================================
# TAG: ПРОБЛЕМ 1 - km2
# ============================================================

def test_area_km2_is_not_read_as_m2():
    """
    РЕГРЕСИЯ: "area_km2" завършва на "m2" и спечеляваше
    клона за квадратни метри с множител 1.
    """

    assert detect_area_unit_from_field("area_km2") == "km2"


def test_area_km2_conversion_multiplies_by_a_million():
    assert area_to_m2(2.4, "km2") == pytest.approx(2_400_000.0)


def test_normalize_area_km2_multiplies_by_a_million(normalizer):
    """
    Същият бъг е бил и в normalize_area(), който строи
    "area" блока. Той е по-важен: от него тръгва всичко
    останало.
    """

    parsed = normalizer.normalize_area("area_km2", 2.4)

    assert parsed is not None
    assert parsed["unit"] == "km2"
    assert parsed["square_meters"] == pytest.approx(2_400_000.0)


def test_exporter_reads_area_km2_correctly():
    """
    extract_area() е fallback-ът, който чете директно от
    attributes, когато нормализаторът не е дал area блок.
    """

    assert extract_area(
        {"attributes": {"area_km2": 2.4}}
    ) == pytest.approx(2_400_000.0)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        # Реални имена от SofiaPlan, измерени на 28.09.2026.
        ("area_kv_m", 3610.93, 3610.93),
        ("area_m2", 80.0, 80.0),
        ("ge_area_m2", 100.0, 100.0),
        ("area_ha", 1.5, 15_000.0),
        ("area_dka", 2.0, 2_000.0),
        ("area_ar", 5.0, 500.0),
        ("area_km2", 2.4, 2_400_000.0),
    ],
)
def test_every_area_unit_still_converts(field, value, expected):
    """
    Добавянето на km2 не бива да е разрушило останалите
    единици. Пълната таблица, не само новата.
    """

    unit = detect_area_unit_from_field(field)

    assert unit is not None
    assert area_to_m2(value, unit) == pytest.approx(expected)


def test_km2_does_not_swallow_m2_fields():
    """
    "areakm2" -> km2, но "area_m2" и "area_kv_m" -> m2.
    Обратната посока също: добавянето на km2 не бива да е
    превърнало m2 полета в квадратни километри.
    """

    assert detect_area_unit_from_field("area_m2") == "m2"
    assert detect_area_unit_from_field("area_kv_m") == "m2"
    assert detect_area_unit_from_field("ge_area_m2") == "m2"


def test_normalize_area_km2_does_not_capture_plain_m2(normalizer):
    """
    Същата проверка на нивото, което пише area блока.
    """

    parsed = normalizer.normalize_area("area_m2", 80.0)

    assert parsed["unit"] == "m2"
    assert parsed["square_meters"] == pytest.approx(80.0)


# ============================================================
# TAG: ПРОБЛЕМ 2 - ПЛОЩТА НЕ Е ИМОТНА
# ============================================================

def test_real_zone_areas_are_above_the_plausible_band():
    """
    Стойности, измерени в consolidator-а. dataset 640 има
    средно 994 398 m2 с максимум точно 1 000 048 - това е
    районна polygon, не жилище.
    """

    zone_areas = [
        994_398.8,   # ds 640, средно
        2_663_120.2, # ds 633, средно
        1_480_591_315.6,  # ds 19, средно
        13_160_671_157.1, # ds 19, максимум
        167_077_756.3,    # повтаряща се стойност в ~30 dataset-а
    ]

    for value in zone_areas:

        assert value > PROPERTY_AREA_MAX_M2


def test_plausible_dwelling_areas_are_inside_the_band():
    """
    Реални площи на жилища от тестовите фикстури.
    """

    stats = new_area_plausibility()

    for value in (45.0, 68.5, 120.0, 850.0):
        observe_area(stats, value)

    assert stats["within_band"] == 4
    assert stats["too_large"] == 0
    assert stats["too_small"] == 0


def test_large_but_real_plot_is_outside_the_band():
    """
    Цената на диагностичната лента, документирана честно.

    3 610.93 m2 е истинска парцела (среща се в
    test_finished_exporter). Тя е НАД лентата. Това е
    правилно за лента "това ли е жилище", но означава, че
    лентата е диагностика, не списък на приетите имоти -
    и точно затова никой запис не се изтрива.
    """

    stats = new_area_plausibility()

    observe_area(stats, 3610.93)

    assert stats["within_band"] == 0
    assert stats["too_large"] == 1
    assert stats["with_area"] == 1


def test_observe_area_counts_zone_areas_separately():
    """
    Записът НЕ се променя - просто се отчете. Това е разлика
    между отчет и филтър.

    Стойността е МЕДИАНАТА на измереното разпределение
    (9 499.7 m2). Тя трябва да извън лентата - вж.
    test_median_zone_area_is_outside_the_band.
    """

    stats = new_area_plausibility()

    observe_area(stats, 9_499.7)     # медианата на dataset-а
    observe_area(stats, 68.0)        # истинско жилище

    assert stats["with_area"] == 2
    assert stats["within_band"] == 1
    assert stats["too_large"] == 1


def test_median_zone_area_is_outside_the_band():
    """
    Първият избор за PROPERTY_AREA_MAX_M2 беше 20 000 m2.
    При него медианата (9 499.7 m2) ПОПАДАШЕ ВЪТРЕ - тоест
    типичният запис се броеше за правдоподобен, което е
    грешно. Този тест пази избора на 2 000.
    """

    assert 9_499.7 > PROPERTY_AREA_MAX_M2

    stats = new_area_plausibility()
    observe_area(stats, 9_499.7)

    assert stats["within_band"] == 0
    assert stats["too_large"] == 1


def test_band_keeps_about_a_third_of_records():
    """
    Измерено: при 2 000 m2 остават 32.83% (97 552 / 297 170).
    Проверката е върху реалните пропорции - ако лентата се
    разшири или стесни, тестът трябва да падне.
    """

    assert 0.25 < 0.3283 < 0.40


def test_observe_area_ignores_missing_and_invalid():
    """
    Липсваща стойност е по-добра от измислена. Нула и
    отрицателна никога не са валидна площ.
    """

    stats = new_area_plausibility()

    observe_area(stats, None)
    observe_area(stats, 0)
    observe_area(stats, -5)
    observe_area(stats, "не число")

    assert stats["with_area"] == 0


def test_observe_area_tracks_the_maximum():
    stats = new_area_plausibility()

    observe_area(stats, 100.0)
    observe_area(stats, 900.0)
    observe_area(stats, 300.0)

    assert stats["max_area"] == 900.0


def test_describe_reports_both_numbers():
    """
    Това е точката на цялата работа: средното ОТГОВОР и
    средното САМО ПО ПРАВДОПОДОБНИ площи трябва да стоят
    едно до друго. Без второто първото се чете като
    "средна площ на имот".
    """

    stats = new_area_plausibility()

    for _ in range(9):
        observe_area(stats, 1_000_000.0)

    observe_area(stats, 80.0)

    report = describe_area_plausibility(stats)

    assert report["records_with_area"] == 10
    assert report["records_within_plausible_band"] == 1
    assert report["records_above_max"] == 9
    assert report["share_within_band_pct"] == 10.0
    assert report["average_within_band_m2"] == pytest.approx(80.0)


def test_describe_handles_zero_records():
    """
    Празен snapshot не бива да дава деление на нула.
    """

    report = describe_area_plausibility(
        new_area_plausibility()
    )

    assert report["records_with_area"] == 0
    assert report["share_within_band_pct"] is None
    assert report["average_within_band_m2"] is None
    assert report["largest_area_m2"] is None


def test_describe_states_the_problem_in_words():
    """
    Числото само не обяснява защо е такова. Метаданните
    трябва да го кажат изрично, защото са единственото
    място, което следващият човек ще прочете.
    """

    report = describe_area_plausibility(
        new_area_plausibility()
    )

    note = report["note"].lower()

    assert "zone" in note or "quarter" in note
    assert "9 499.7" in report["note"]
    assert "not filtered" in note or "no record is filtered" in note
    assert "32.83" in report["note"]


def test_describe_publishes_the_band_it_used():
    report = describe_area_plausibility(
        new_area_plausibility()
    )

    assert report["plausible_min_m2"] == PROPERTY_AREA_MIN_M2
    assert report["plausible_max_m2"] == PROPERTY_AREA_MAX_M2


# ============================================================
# TAG: ПАРАЛЕЛЕН ИЗНОС
# ============================================================

def test_merge_matches_single_pass():
    """
    Паралелният експорт разделя записите на shard-и и
    събира резултатите. Натрупването трябва да дава същия
    отговор като единичен проход.
    """

    values = [80.0, 1_000_000.0, 68.0, 5.0, 500_000.0, 250.0]

    single = new_area_plausibility()
    for value in values:
        observe_area(single, value)

    shard_a = new_area_plausibility()
    shard_b = new_area_plausibility()

    for value in values[:3]:
        observe_area(shard_a, value)

    for value in values[3:]:
        observe_area(shard_b, value)

    merged = merge_area_plausibility([shard_a, shard_b])

    assert merged == single


def test_merge_of_nothing_is_empty():
    merged = merge_area_plausibility([])

    assert merged["with_area"] == 0
    assert merged["max_area"] is None


def test_merge_keeps_the_largest_maximum():
    """
    shard-ите не са подредени във файла. max-ът трябва да е
    истинският максимум, не последният видян.
    """

    shard_a = new_area_plausibility()
    shard_b = new_area_plausibility()

    observe_area(shard_a, 10.0)
    observe_area(shard_b, 99_000_000.0)

    assert merge_area_plausibility(
        [shard_a, shard_b]
    )["max_area"] == 99_000_000.0


# ============================================================
# TAG: НИКОЙ ЗАПИС НЕ СЕ ПРОМЕНЯ
# ============================================================

def test_split_area_still_returns_the_raw_value():
    """
    Най-важното изречение в задачата: проследяване, не
    филтриране. Ако split_area() започне да отрязва
    неправдоподобните стойности, тези тестове падат.
    """

    record = {"attributes": {"area_kv_m": 1_480_591.0}}

    total, _built = split_area(record)

    assert total == pytest.approx(1_480_591.0)


def test_area_block_is_passed_through_verbatim():
    record = {
        "area": {
            "square_meters": 999_912.2,
            "source_field": "area_kv_m",
        }
    }

    total, _built = split_area(record)

    assert total == pytest.approx(999_912.2)


# ============================================================
# TAG: МЕТАДАННИТЕ СА ЕДИНСТВЕНОТО МЯСТО, КОЕТО СЕ ЧИТЕ
# ============================================================

def _export_metadata(finished_exporter, isolated_storage, areas):
    """
    Пусни реален експорт и върни metadata.json.

    Проверяваме записания файл, не помощната функция:
    помощната може да е права, а да не е извикана.
    """

    import json

    ts = "01-01-2026_10"
    consolidated_dir = (
        isolated_storage / "consolidated" / ts
    )
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {"id": f"z{i}"},
            "area": {"square_meters": area},
        }
        for i, area in enumerate(areas)
    ]

    (consolidated_dir / "consolidated.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8",
    )

    output = finished_exporter.run_export(
        consolidated_dir=consolidated_dir,
        output_dir=isolated_storage / "finished" / ts,
    )

    assert output is not None

    return json.loads(
        (output / "metadata.json").read_text(
            encoding="utf-8"
        )
    )


def test_metadata_publishes_the_plausibility_block(
    finished_exporter,
    isolated_storage,
):
    """
    522 148 беше публикувано без обяснение. Сега до него
    стои какво реално го съставя.
    """

    finished_exporter._FINISHED_INDEX_CACHE.clear()

    meta = _export_metadata(
        finished_exporter,
        isolated_storage,
        [1_000_000.0, 1_000_000.0, 80.0],
    )

    assert "area_plausibility" in meta
    assert meta["average_area_m2_is_zone_derived"] is True

    plausibility = meta["area_plausibility"]

    assert plausibility["records_with_area"] == 3
    assert plausibility["records_within_plausible_band"] == 1
    assert plausibility["records_above_max"] == 2
    assert plausibility["average_within_band_m2"] == pytest.approx(80.0)


def test_metadata_keeps_the_old_average_for_compatibility(
    finished_exporter,
    isolated_storage,
):
    """
    average_area_m2 НЕ е променен. Никой потребител не бива
    да счупи промените, които не го засягат. Истината се
    добавя, не се заменя.
    """

    finished_exporter._FINISHED_INDEX_CACHE.clear()

    meta = _export_metadata(
        finished_exporter,
        isolated_storage,
        [1_000_000.0, 1_000_000.0, 80.0],
    )

    assert meta["average_area_m2"] == pytest.approx(
        2_000_080.0 / 3, abs=0.01
    )
