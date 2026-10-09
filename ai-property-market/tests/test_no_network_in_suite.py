"""
Регресии около това, че тестовият рън не бива да пипа
реалните storage/ и storage_raw/ папки.

История, измерена на 07.10.2026:

tests/test_parallel_runner.py имаше тест, който викаше
dataset_fetcher._download_once() с истински urlopen и без
monkeypatch на RAW_DIR. Той записваше в реалната
storage_raw/ папка и наистина правеше мрежова заявка към
api.sofiaplan.bg.

Последицата не беше козметична. Нормалайзерът групира
snapshot-ите по (source, dataset_id) и взима НАЙ-новия по
mtime (normalizer.py:1401). Тестът беше записал dataset 1
в storage_raw/sofiaplan/01-01-2026_10/ с файл
{"type": "FeatureCollection", "features": []} - празен.

При следващ `python main.py --normalize` dataset 1 ставаше
ТОЗИ файл, каталогът очакваше 9 записа, файлът даваше 0 и
записът падаше с:

    ValueError: Expected record count mismatch:
                expected=9, actual=0

Тоест един тест мълчаливо саботираше пайплайна. Данните
не бяха повредени, но dataset 1 изчезваше от изхода при
всяко нормализиране.

Същият клас проблем се повтори и в storage/: вторият тест
по-долу пази условието, открито при подготовката за
публично разпространяване на проекта.

Тук пазим условието, не конкретния тест.
"""

import json
import zipfile
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# TAG: _download_once пише само там, където му е казано
# ============================================================

class _FakeResponse:
    """
    Минимален отговор на urlopen. dataset_fetcher чете
    само Content-Type и read().
    """

    def __init__(self, payload: bytes, content_type: str):
        self._payload = payload
        self._pos = 0
        self.headers = {"Content-Type": content_type}

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._pos:]
            self._pos = len(self._payload)
            return chunk
        chunk = self._payload[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_once_writes_only_into_tmp_path(
    monkeypatch,
    tmp_path,
    network_allowed
):
    """
    С мокнат HTTP _download_once() трябва да пише само в
    tmp_path - дори когато наборът от данни е такъв, че
    нормалайзерът после ще го прочете.
    """

    from tools import dataset_fetcher as mod

    root = tmp_path / "raw"

    monkeypatch.setattr(mod, "RAW_DIR", root)

    payload = json.dumps(
        {"type": "FeatureCollection", "features": []}
    ).encode("utf-8")

    monkeypatch.setattr(
        mod,
        "urlopen",
        lambda *a, **k: _FakeResponse(
            payload, "application/json"
        ),
    )

    ok, info = mod._download_once(
        999, "sofiaplan", "01-01-2026_10"
    )

    assert ok is True

    written = Path(info["raw_file"])

    assert str(written).startswith(str(tmp_path)), (
        f"запис извън tmp_path: {written}"
    )

    assert written.exists()


def test_no_sofiaplan_dataset_999_in_real_raw_dir():
    """
    Ако някой тест пак започне да пише в реалната папка,
    999-тестовият dataset ще се появи там. Това е най-евтиният
    детектор и не изисква monkeypatch.
    """

    real_raw = BASE_DIR / "storage_raw"

    if not real_raw.exists():
        pytest.skip("storage_raw липсва")

    leaked = list(
        real_raw.rglob("datasets/999")
    )

    assert not leaked, (
        "dataset 999 е изтекъл в реалната storage_raw/: "
        f"{leaked}"
    )


def test_real_raw_dir_has_no_future_test_snapshot():
    """
    "01-01-2026_10" е timestamp, използван от тестовете за
    синтетични snapshot-и. Ако тази папка се появи в
    реалната storage_raw/, някой тест пише в нея.
    """

    real_snapshot = (
        BASE_DIR / "storage_raw" / "sofiaplan" / "01-01-2026_10"
    )

    assert not real_snapshot.exists(), (
        f"тестов snapshot в реалната storage_raw/: {real_snapshot}"
    )


# ============================================================
# TAG: Същото условие, но за storage/
# ============================================================

def test_real_storage_has_no_test_snapshot():
    """
    Същият проблем, открит от другата страна.

    tests/test_finished_exporter.py вика run_export(), който
    пише storage/finished/<timestamp>/. Timestamp-ът е тестов
    ("01-01-2026_10") и lexicographically е СЛЕД реалните
    ("09-10-2026_17"), затова find_latest_finished_snapshot()
    избира тестовата папка.

    Последица: dashboard-ът показва два синтетични имота от
    тестовете вместо резултата от истинския pipeline. Не е
    козметично и не се самопоправя - папката остава.
    """

    real_finished = BASE_DIR / "storage" / "finished"

    if not real_finished.exists():
        pytest.skip("storage/finished липсва")

    leaked = [
        p for p in real_finished.iterdir()
        if p.is_dir() and p.name == "01-01-2026_10"
    ]

    assert not leaked, (
        "тестова снимка в реалната storage/finished/: "
        f"{leaked}"
    )


# ============================================================
# TAG: describe_rejection не пада на ZIP
# ============================================================

def test_describe_rejection_classifies_zip_without_crashing(
    tmp_path
):
    """
    Регресия, измерена на 07.10.2026.

    describe_rejection() присвояваше `container` само в
    клона `if detected_format not in {"zip", "gzip"}`.
    За ZIP файловете клонът не се изпълняваше, а след
    него `container` се чешеше -> UnboundLocalError.

    Засегнати бяха 8 dataset-а (502, 525, 529, 530, 536,
    537, 644, 646). Отхвърлянето им беше ПРАВИЛНО, но
    некласифицирано, тоест изглеждаше като загуба на
    данни - точно проблемът, за който съществува
    describe_rejection().
    """

    from tools import normalizer as norm

    path = tmp_path / "raw_data.bin"

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sheet.csv", "a,b\n1,2\n")

    detected = norm.sniff_file(path)

    assert detected == "zip"

    detail = norm.describe_rejection(path, detected)

    assert detail["container"] == "zip"
    assert detail["property_candidate"] is False
    assert "ZIP" in detail["label"]


def test_describe_rejection_still_names_binary_containers(
    tmp_path
):
    """
    Същата функция, но за пътя, който вече работеше:
    container се чете от водещите байтове. Този тест пази
    да не е счупен, докато горният поправя кривия.
    """

    from tools import normalizer as norm

    path = tmp_path / "raw_data.bin"

    path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

    detected = norm.sniff_file(path)

    detail = norm.describe_rejection(path, detected)

    assert detail["container"] == "pdf"
    assert detail["property_candidate"] is False


# ============================================================
# TAG: празен snapshot не бива да мине тихо
# ============================================================

def test_expected_count_mismatch_is_not_silently_accepted(
    tmp_path
):
    """
    Проверката, която хвана dataset 1.

    Празен FeatureCollection трябва да бъде отхвърлен, ако
    каталогът очаква 9 записа. Ако някой ден тази проверка
    отпадне, dataset 1 ще влезе с 0 записа и загубата ще
    стане невидима.
    """

    from tools import normalizer as norm

    output = tmp_path / "dataset_1.json"

    output.write_text(
        '[{"a":1}]', encoding="utf-8"
    )

    result = norm.validate_output(
        output, expected_count=9
    )

    assert result["valid"] is False
    assert result["count"] == 1
    assert "mismatch" in result["error"]


def test_empty_geojson_snapshot_is_measurable(tmp_path):
    """
    Файлът, който тестът беше записал в реалната папка.
    Вижда се от normalizer, че е валиден GeoJSON - не
    празен, не бинарен - и точно затова е опасен.
    """

    from tools import normalizer as norm

    path = tmp_path / "raw_data.geojson"

    path.write_bytes(
        b'{"type": "FeatureCollection", "features": []}'
    )

    assert norm.sniff_file(path) == "geojson"

    records = list(norm.iter_raw_records(path))

    assert records == []