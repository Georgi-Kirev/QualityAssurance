# TAG: TESTS - FORMAT DETECTION & RAW FILE DISCOVERY
#
# Два бъга, които заедно направиха целия ценови слой да изчезне
# МЪЛЧАЩО от продукта:
#
# 1. detect_format() се опитваше да направи json.loads() върху
#    ОТРЯЗЪК от 64 KB. Всеки JSON файл над 64 KB е прерязан =>
#    JSONDecodeError => "txt"/"bin". Dataset 624
#    (ikonomika.imoti_ceni_ge, единственият с цени, 9.9 MB) се
#    записваше като raw_data.txt.
#
# 2. normalizer.RAW_FILENAMES не съдържаше "raw_data.txt", затова
#    144 свалени dataset-а (включително 624) не бяха нормализирани.
#    Нямаше грешка, няма предупреждение - просто липсваха.
#
# Комбиниран ефект: пайплайнът завършваше "успешно" с
# "Properties with price: 0".

import json

import pytest

from tools.dataset_fetcher import (
    FORMAT_SNIFF_BYTES,
    detect_format,
)
from tools import normalizer


# ============================================================
# TAG: LARGE JSON IS STILL JSON
# ============================================================

def _big_geojson(extra_features: int = 4000) -> bytes:
    """GeoJSON, който е значително по-голям от sniff прага."""
    return json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "object_id": i,
                    "regname": f"ж.к. Зона {i}",
                    "cena_ap": 100000 + i,
                    "area_kv_m": 5000 + i,
                    # пълнеж, за да надхвърли 64 KB
                    "pad": "x" * 40,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [23.3 + i * 1e-6, 42.7],
                },
            }
            for i in range(extra_features)
        ],
    }).encode("utf-8")


def test_large_geojson_is_not_mistaken_for_txt():
    """
    РЕГРЕСИЯ: точно този случай даваше "txt".
    """
    payload = _big_geojson()

    assert len(payload) > FORMAT_SNIFF_BYTES
    assert detect_format(payload, "text/plain;charset=UTF-8") == "geojson"


def test_large_geojson_via_application_octet_stream():
    """
    Сървърът на SofiaPlan обслужва 624 като text/plain, но
    други източници може да дават octet-stream. И двата
    случая трябва да се разпознаят.
    """
    payload = _big_geojson()

    assert detect_format(payload, "application/octet-stream") == "geojson"
    assert detect_format(payload, "") == "geojson"


def test_truncated_prefix_is_all_we_get():
    """
    Документира реалното ограничение: detect_format() вижда само
    първите 64 KB. Това е достатъчно за GeoJSON, защото
    FeatureCollection стои в началото.
    """
    payload = _big_geojson()
    prefix = payload[:FORMAT_SNIFF_BYTES]

    assert detect_format(prefix, "text/plain") == "geojson"


def test_small_geojson_still_detected():
    payload = json.dumps({
        "type": "FeatureCollection",
        "features": [],
    }).encode("utf-8")

    assert detect_format(payload, "application/geo+json") == "geojson"


def test_plain_json_object_detected():
    payload = json.dumps({"a": 1, "b": 2}).encode("utf-8")

    assert detect_format(payload, "application/json") == "json"


def test_large_plain_json_array_detected():
    payload = json.dumps([{"x": "y" * 50} for _ in range(3000)]).encode("utf-8")

    assert len(payload) > FORMAT_SNIFF_BYTES
    assert detect_format(payload, "application/json") == "json"


# ============================================================
# TAG: NON-JSON STILL WORKS
# ============================================================

def test_csv_detected_from_content_type():
    assert detect_format(b"a,b,c\n1,2,3", "text/csv") == "csv"


def test_zip_detected_from_content_type():
    assert detect_format(b"PK\x03\x04junk", "application/zip") == "zip"


def test_binary_detected_by_null_bytes():
    assert detect_format(b"\x00\x01\x02\x03", "application/octet-stream") == "bin"


def test_binary_detected_by_undecodable_bytes():
    assert detect_format(b"\xff\xfe\xfd\xfc", "text/plain") == "bin"


def test_raster_like_text_is_bin():
    # DTM/raster файлове - текст, но не JSON.
    assert detect_format(b"NCOLS 100\nNROWS 100\nx y z\n", "text/plain") == "bin"


# ============================================================
# TAG: NORMALIZER MUST SEE raw_data.txt
# ============================================================

def test_txt_is_a_known_raw_filename():
    """
    РЕГРЕСИЯ: 624 се записваше като raw_data.txt и после
    игнорираше се от discovery-то.
    """
    assert "raw_data.txt" in normalizer.RAW_FILENAMES


@pytest.fixture
def isolated_raw_dir(tmp_path, monkeypatch):
    """
    discover_raw_snapshots() чете от module-level RAW_DIR.
    Без това изолиран тестът вижда реалните 370 файла от
    последния рън.
    """
    fake = tmp_path / "storage_raw"
    fake.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(normalizer, "RAW_DIR", fake)
    return fake


def test_discovery_accepts_txt_file(isolated_raw_dir):
    """
    Край-to-край: файл, наречен raw_data.txt, трябва да бъде
    открит от snapshot discovery-то.
    """
    dataset_dir = (
        isolated_raw_dir / "sofiaplan" / "28-09-2026_14"
        / "datasets" / "624"
    )
    dataset_dir.mkdir(parents=True)

    (dataset_dir / "raw_data.txt").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"dataset_id": 624, "source": "sofiaplan"}),
        encoding="utf-8",
    )

    found = normalizer.discover_raw_snapshots()

    assert len(found) == 1
    assert str(found[0]["dataset_id"]) == "624"
    assert found[0]["raw_file"].name == "raw_data.txt"


def test_discovery_accepts_future_extension(isolated_raw_dir):
    """
    Нов формат не бива да носи мълчалива загуба на данни.
    """
    dataset_dir = (
        isolated_raw_dir / "sofiaplan" / "28-09-2026_14"
        / "datasets" / "7"
    )
    dataset_dir.mkdir(parents=True)

    (dataset_dir / "raw_data.ndjson").write_text("{}", encoding="utf-8")
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"dataset_id": 7, "source": "sofiaplan"}),
        encoding="utf-8",
    )

    found = normalizer.discover_raw_snapshots()

    assert len(found) == 1


def test_discovery_still_ignores_unrelated_files(isolated_raw_dir):
    """
    Отвореността не бива да е безразборна.
    """
    dataset_dir = (
        isolated_raw_dir / "sofiaplan" / "28-09-2026_14"
        / "datasets" / "7"
    )
    dataset_dir.mkdir(parents=True)

    (dataset_dir / "some_other_file.txt").write_text("{}", encoding="utf-8")
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"dataset_id": 7, "source": "sofiaplan"}),
        encoding="utf-8",
    )

    assert normalizer.discover_raw_snapshots() == []


# ============================================================
# TAG: SNIFF IS CONTENT-BASED
# ============================================================

def test_sniff_file_ignores_extension(tmp_path):
    """
    Файл с .txt разширение, но GeoJSON съдържание -> geojson.
    """
    path = tmp_path / "raw_data.txt"
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    assert normalizer.sniff_file(path) == "geojson"
