"""
Тестове за tools/dataset_fetcher.py

HTTP-то е МОКНАТО - тестовете не правят мрежови заявки.
"""

import json
import re
from pathlib import Path

import pytest


# ============================================================
# TAG: detect_format
# ============================================================

def test_detect_format_geojson(fetcher):
    data = b'{"type":"FeatureCollection","features":[]}'

    result = fetcher.detect_format(
        data, "application/geo+json"
    )

    assert result == "geojson"


def test_detect_format_json_object(fetcher):
    data = b'{"a":1}'

    assert fetcher.detect_format(
        data, "application/json"
    ) == "json"


def test_detect_format_csv_by_content_type(fetcher):
    data = b"a,b,c\n1,2,3\n"

    assert fetcher.detect_format(
        data, "text/csv"
    ) == "csv"


def test_detect_format_zip(fetcher):
    assert fetcher.detect_format(
        b"PK\x03\x04", "application/zip"
    ) == "zip"


def test_detect_format_binary_is_safe(fetcher):
    data = b"\x00\x01\x02\x03"

    result = fetcher.detect_format(
        data, "application/octet-stream"
    )

    assert isinstance(result, str)
    assert len(result) > 0


def test_detect_format_never_raises(fetcher):
    for payload in (b"", b"{", b"\xff\xfe\x00", b"[1,2,"):
        assert isinstance(
            fetcher.detect_format(
                payload, "application/octet-stream"
            ),
            str
        )


# ============================================================
# TAG: _download_once - MOCKED HTTP
# ============================================================

class _FakeResponse:
    def __init__(self, payload: bytes, content_type: str = "application/geo+json"):
        self._payload = payload
        self.headers = {"Content-Type": content_type}
        self._pos = 0

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


@pytest.fixture
def fetcher(monkeypatch, network_allowed):
    from tools import dataset_fetcher as mod

    monkeypatch.setattr(
        mod, "SOFIAPLAN_API",
        "https://api.sofiaplan.bg"
    )
    monkeypatch.setattr(
        mod.time, "sleep", lambda *_: None
    )
    monkeypatch.setattr(
        mod, "_wait_for_rate_limit", lambda: None
    )

    return mod


@pytest.fixture
def fetcher_raw_dir(fetcher, monkeypatch, tmp_path):
    root = tmp_path / "raw"
    monkeypatch.setattr(
        fetcher, "RAW_DIR", root
    )
    return root


def test_download_once_writes_file_and_metadata(
    fetcher,
    fetcher_raw_dir
):
    payload = json.dumps({
        "type": "FeatureCollection",
        "features": []
    }).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse(payload)

    fetcher.urlopen = fake_urlopen

    ok, info = fetcher._download_once(
        999, "sofiaplan", "01-01-2026_10"
    )

    assert ok is True
    assert info["format"] == "geojson"
    assert info["size_bytes"] == len(payload)

    raw_file = Path(info["raw_file"])

    assert raw_file.exists()
    assert raw_file.parent.name == "999"
    assert raw_file.name == "raw_data.geojson"

    written = json.loads(
        raw_file.read_text(encoding="utf-8")
    )

    assert written["type"] == "FeatureCollection"

    metadata_file = (
        raw_file.parent / "metadata.json"
    )

    assert metadata_file.exists()

    metadata = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )

    assert metadata["dataset_id"] == 999
    assert metadata["detected_format"] == "geojson"
    assert metadata["file_size_bytes"] == len(payload)

    assert captured["url"] == "https://api.sofiaplan.bg/999"


def test_download_once_unknown_source(
    fetcher,
    fetcher_raw_dir
):
    ok, error = fetcher._download_once(
        1, "nope", "01-01-2026_10"
    )

    assert ok is False
    assert "Unknown source" in str(error)


def test_download_once_http_error_returns_false(
    fetcher,
    fetcher_raw_dir,
    monkeypatch
):
    def fail(request, timeout=None):
        raise fetcher.HTTPError(
            request.full_url, 500, "Server Error", {}, None
        )

    monkeypatch.setattr(
        fetcher, "urlopen", fail
    )

    ok, error = fetcher._download_once(
        999, "sofiaplan", "01-01-2026_10"
    )

    assert ok is False
    assert "500" in str(error)


def test_download_once_no_partial_file_on_error(
    fetcher,
    fetcher_raw_dir,
    monkeypatch
):
    def fail(request, timeout=None):
        raise fetcher.URLError("boom")

    monkeypatch.setattr(
        fetcher, "urlopen", fail
    )

    ok, _error = fetcher._download_once(
        999, "sofiaplan", "01-01-2026_11"
    )

    assert ok is False

    dataset_dir = (
        fetcher_raw_dir / "sofiaplan" / "01-01-2026_11"
        / "datasets" / "999"
    )

    assert not list(
        dataset_dir.glob("raw_data.*")
    ), "не бива да остава празен/частичен файл"


# ============================================================
# TAG: download_dataset - RETRY
# ============================================================

def test_download_dataset_retries_then_succeeds(
    fetcher,
    fetcher_raw_dir,
    monkeypatch
):
    payload = b'[{"a":1}]'

    attempts = {"n": 0}

    def flaky(request, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise fetcher.URLError("connection reset")
        return _FakeResponse(
            payload, "application/json"
        )

    monkeypatch.setattr(
        fetcher, "urlopen", flaky
    )

    result = fetcher.download_dataset(
        999, "sofiaplan", "01-01-2026_12"
    )

    assert result is not None
    assert Path(result).exists()
    assert attempts["n"] == 3


def test_download_dataset_gives_up_returns_none(
    fetcher,
    fetcher_raw_dir,
    monkeypatch
):
    attempts = {"n": 0}

    def always_fail(request, timeout=None):
        attempts["n"] += 1
        raise fetcher.URLError("permanent failure")

    monkeypatch.setattr(
        fetcher, "urlopen", always_fail
    )

    result = fetcher.download_dataset(
        999, "sofiaplan", "01-01-2026_13"
    )

    assert result is None
    assert attempts["n"] == fetcher.MAX_ATTEMPTS


def test_download_dataset_does_not_retry_unknown_source(
    fetcher,
    fetcher_raw_dir
):
    result = fetcher.download_dataset(
        1, "nope", "01-01-2026_14"
    )

    assert result is None


# ============================================================
# TAG: TIMESTAMP
# ============================================================

def test_current_timestamp_format(fetcher):
    stamp = fetcher.current_timestamp()

    assert re.fullmatch(
        r"\d{2}-\d{2}-\d{4}_\d{2}", stamp
    ), f"лош timestamp формат: {stamp}"
