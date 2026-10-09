# TAG: TESTS - TRUNCATED SNIFF SAMPLE
#
# Третият молчалив бъг в същия ред, който вече беше
# изчистен два пъти.
#
# Първият (27.09.2026): normalizer.RAW_FILENAMES нямаше
#   "raw_data.txt" -> 144 dataset-а се сваляха и мълчаха.
# Вторият (28.09.2026): dataset_fetcher.detect_format()
#   правеше json.loads() върху 64 KB префикс -> 624 ставаше
#   "txt". Виж test_format_detection.py.
# Третият (28.09.2026, този файл): normalizer.sniff_file()
#   чете ровно FORMAT_SNIFF_BYTES (1 MiB) и го декодира
#   СТРИКТНО. Ако байтът на границата е първата половина на
#   двубайтов UTF-8 символ, decode-ът пада - НЕ защото
#   файлът е двоичен, а защото пробата е отрязана.
#
# Падащият decode после опитваше UTF-16, който "успява"
# върху боклук, връща безсмислица, която не минава нито
# JSON, нито CSV проверката, и файлът получава етикет "txt".
# Всичко това БЕЗ грешка и БЕЗ запис в логовете.
#
# ИЗМЕРЕНО на реалните данни (storage_raw/sofiaplan/
# 28-09-2026_14), а не推测ено:
#
#     ds=181  9 137 325 B   31 244 features
#     ds=274  3 815 919 B   17 610 features
#     ds=479 38 364 927 B   60 494 features
#     ds=568  4 817 513 B    9 074 features
#     --------------------------
#     118 422 features, 56.1 MB, мълчаливо загубени
#
# И четирите файла започват точно така:
#     {"type": "FeatureCollection", "features": [{"typ
# Тоест те НЕ са били никога "txt". Те са били GeoJSON и
# лошият decode ги е преименувал.

import json

import pytest

from tools import normalizer
from tools.normalizer import (
    FORMAT_SNIFF_BYTES,
    classify_binary_container,
    decode_text_sample,
    describe_rejection,
    iter_raw_records,
    sniff_file,
)


# ============================================================
# TAG: TEST DATA BUILDER
# ============================================================

def _geojson_with_cyrillic_at_the_boundary() -> bytes:
    """
    Build a valid GeoJSON FeatureCollection whose byte at
    position FORMAT_SNIFF_BYTES - 1 is the FIRST half of a
    two-byte UTF-8 character.

    That is the exact shape of datasets 181/274/479/568.
    """

    head = (
        '{"type": "FeatureCollection", "features": ['
        '{"type": "Feature", "properties": {"regname": "'
    )

    # Pad with ASCII up to one byte before the boundary.
    pad_needed = FORMAT_SNIFF_BYTES - 1 - len(head.encode("utf-8"))
    assert pad_needed > 0

    payload = head + ("a" * pad_needed)

    # "я" is U+044F -> 0xD1 0x8F in UTF-8, two bytes.
    # Its FIRST byte is what lands on the boundary.
    cyrillic = "я".encode("utf-8")
    assert len(cyrillic) == 2
    assert cyrillic[0] == 0xD1

    tail = (
        '"}, "geometry": {"type": "Point", '
        '"coordinates": [23.3, 42.7]}}]}'
    )

    return payload.encode("utf-8") + cyrillic + tail.encode("utf-8")


@pytest.fixture
def truncated_boundary_file(tmp_path):
    """
    A file on disk larger than the sniff window, with the
    boundary landing inside a Cyrillic character.
    """

    path = tmp_path / "raw_data.txt"
    path.write_bytes(_geojson_with_cyrillic_at_the_boundary())
    return path


# ============================================================
# #1 THE REGRESSION ITSELF
# ============================================================

def test_boundary_file_is_actually_truncated_mid_character(
    truncated_boundary_file,
):
    """
    Document the precondition.

    If this test ever fails, the reproduction stopped being
    faithful and the rest of the file proves nothing.
    """

    sample = truncated_boundary_file.read_bytes()[
        :FORMAT_SNIFF_BYTES
    ]

    assert len(sample) == FORMAT_SNIFF_BYTES

    # Strict decode MUST fail - this is the bug's mechanism.
    with pytest.raises(UnicodeDecodeError):
        sample.decode("utf-8-sig")

    # The last byte is a UTF-8 continuation-sequence start.
    assert sample[-1] == 0xD1


def test_truncated_geojson_is_not_labelled_txt(
    truncated_boundary_file,
):
    """
    РЕГРЕСИЯ: този файл се разпознаваше като "txt" и
    отпадаше от пайплайна без следа.
    """

    assert sniff_file(truncated_boundary_file) == "geojson"


def test_truncated_geojson_is_readable_end_to_end(
    truncated_boundary_file,
):
    """
    Не е достатъчно да се разпознае - записите трябва и да
    се прочетат. Проверява и броя.
    """

    records = list(iter_raw_records(truncated_boundary_file))

    assert len(records) == 1
    assert records[0]["type"] == "Feature"


# ============================================================
# #2 THE FALLBACK THAT COULD NOT FAIL
# ============================================================

def test_utf16_fallback_needs_a_real_bom():
    """
    Причината за мълчаливото отпадане беше fallback-ът, който
    не може да се провали.

    Старият код падаше на UTF-16 при ВСЕКА неуспешна проба
    utf-8. Но UTF-16 decode на почти всеки байтов низ е
    "успешен" - връща безсмислен текст, който после не минава
    нито JSON, нито CSV проверката, и файлът получава етикет
    "txt". Една ясна грешка се превръщаше в една неясна.

    Тук: байтове, които utf-16 ДЕЙСТВИТЕЛНО би декодирал без
    грешка, трябва да върнат None.
    """

    # Байтове, които utf-16 ДЕЙСТВИТЕЛНО декодира без
    # грешка, но които utf-8 отхвърля: 0xD1 е начало на
    # многобайтова последователност, а 0x00 не е валидно
    # продължение. Старият код тук "успяваше" и получаваше
    # U+4100 U+4100 ... - безсмислица, която после се
    # преименуваше на "txt".
    decodable_as_utf16 = bytes([0x00, 0xD1] * 40)

    # Предпоставка: utf-16 наистина го приема.
    assert decodable_as_utf16.decode("utf-16") is not None

    assert decode_text_sample(decodable_as_utf16) is None


def test_binary_blob_does_not_decode_as_utf16():
    """
    Двоичен файл трябва да даде "bin", не безсмислен текст.
    """

    # 7z signature - реално от dataset 496.
    sample = b"7z\xbc\xaf\x27\x1c\x00\x04+;r\xc4\xd2\xfa:"

    assert decode_text_sample(sample) is None


def test_decode_text_sample_accepts_truncated_tail():
    """
    Директна проверка на помощника: отрязан край се толерира,
    счупена среда - не.
    """

    # U+20AC е 3 байта в UTF-8 (0xE2 0x82 0xAC). Отрязваме
    # на 2 - точно situationцията "пробата прекъсва символ".
    truncated = "€".encode("utf-8")[:2]

    assert decode_text_sample(truncated) == ""

    # Счупена СРЕДА (не край) пак е двоично.
    assert decode_text_sample(
        b'{"a": "\xff\xfe"}'
    ) is None


def test_decode_text_sample_still_reads_real_utf16():
    """
    Отварянето не е затворено. Истински UTF-16 файл с BOM
    трябва да продължи да се чете.
    """

    payload = '{"type": "FeatureCollection"}'.encode("utf-16")

    assert payload.startswith(b"\xff\xfe")

    text = decode_text_sample(payload)

    assert text is not None
    assert "FeatureCollection" in text


# ============================================================
# #3 BINARY SIGNATURES MUST NOT DEPEND ON A DECODE FAILURE
# ============================================================

@pytest.mark.parametrize(
    "head,expected",
    [
        (b"%PDF-1.7\r%\xe2\xe3\xcf\xd3", "pdf"),
        (b"7z\xbc\xaf\x27\x1c\x00\x04", "7z"),
        (b"II*\x00\x08\x00\x00\x00", "tiff"),
        (b"MM\x00*\x00\x00\x00\x08", "tiff"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole2"),
        (b"PK\x03\x04\x14\x00", "zip"),
        (b"\x1f\x8b\x08\x00", "gzip"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
    ],
)
def test_container_signatures(head, expected):
    """
    Проверено срещу реалните файлове на 28.09.2026:
    4 PDF, 11 7z, 7 GeoTIFF, 2 OLE2, 6+2 ZIP.
    """

    assert classify_binary_container(head) == expected


def test_unknown_binary_is_still_bin():
    assert classify_binary_container(b"\x01\x02\x03\x04") == "bin"


def test_text_is_not_mistaken_for_a_container():
    assert classify_binary_container(
        b'{"type": "FeatureCollection"}'
    ) == "bin"


# ============================================================
# #4 REJECTIONS MUST BE EXPLAINED, NOT JUST COUNTED
# ============================================================

def test_pdf_rejection_names_the_container(tmp_path):
    path = tmp_path / "raw_data.bin"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 64)

    detail = describe_rejection(path, "bin")

    assert detail["container"] == "pdf"
    assert detail["property_candidate"] is False
    assert "PDF" in detail["label"]


def test_tiff_rejection_names_the_container(tmp_path):
    path = tmp_path / "raw_data.bin"
    path.write_bytes(b"II*\x00" + b"\x9f" * 64)

    detail = describe_rejection(path, "bin")

    assert detail["container"] == "tiff"
    assert "raster" in detail["label"].lower()


def test_rejection_never_claims_property_candidate(tmp_path):
    """
    Документ и растър не стават имот. Това е същото
    правило, което държи NON_PROPERTY_SOURCES извън.
    """

    path = tmp_path / "raw_data.bin"
    path.write_bytes(b"%PDF-1.5\n")

    assert describe_rejection(
        path, "bin"
    )["property_candidate"] is False


def test_txt_rejection_is_reported_as_txt(tmp_path):
    path = tmp_path / "raw_data.txt"
    path.write_text("няма таблица тук\n", encoding="utf-8")

    detail = describe_rejection(path, "txt")

    assert detail["container"] == "txt"
    assert detail["detected_format"] == "txt"


# ============================================================
# #5 THE 32 THAT STAY OUT - ON PURPOSE
# ============================================================

def test_binary_datasets_are_still_rejected_by_the_pipeline(
    tmp_path,
):
    """
    32 dataset-а от 369 са PDF / 7z / GeoTIFF / xlsx / .xls /
    docx / QGIS bundle. Те НЕ могат да станат имотни обяви.

    Този тест пази РЕШЕНИЕТО, не бъга: ако някой "поправи"
    пайплайна да ги натика в продукта, тестът пада.
    """

    path = tmp_path / "raw_data.bin"
    path.write_bytes(b"%PDF-1.7\r\n%\xe2\xe3\xcf\xd3\r\n")

    assert sniff_file(path) == "bin"

    with pytest.raises(ValueError):
        next(iter_raw_records(path))


def test_zip_datasets_are_still_rejected(tmp_path):
    """
    PK контейнер -> "zip", който нормализаторът отказва.
    dataset 644/646 са QGIS bundle-и с GeoTIFF вътре.
    """

    path = tmp_path / "raw_data.zip"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    assert sniff_file(path) == "zip"

    with pytest.raises(ValueError):
        next(iter_raw_records(path))
