"""
Общи pytest fixtures за AI Property Market.

Целта е тестовете да НЕ пипат реалните storage/ и
storage_raw/ папки. Всеки тест, който пише, получава
изолиран tmp_path.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ============================================================
# TAG: FILE FIXTURES
# ============================================================

@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def sample_geojson_path() -> Path:
    return FIXTURES_DIR / "sample_geojson.json"


@pytest.fixture(scope="session")
def sample_csv_path() -> Path:
    return FIXTURES_DIR / "sample_csv.csv"


@pytest.fixture(scope="session")
def sample_metadata_path() -> Path:
    return FIXTURES_DIR / "sample_metadata.json"


@pytest.fixture(scope="session")
def sample_geojson() -> dict:
    return json.loads(
        (FIXTURES_DIR / "sample_geojson.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def sample_features(sample_geojson) -> list:
    return sample_geojson["features"]


# ============================================================
# TAG: NETWORK POLICY
# ============================================================

@pytest.fixture
def network_allowed(monkeypatch):
    """
    Отваря мрежевата политика (netpolicy.py) за ЕДИН тест.

    Проектът е изключен по подразбиране - виж netpolicy.py.
    Тестовете, които подменят HTTP слоя с фейк, трябва да
    заявят тази fixture изрично, за да не се чупи
    require_network() преди изобщо да се достигне до фейка.

    Правило: тази fixture се иска САМО от тест, който НЕ
    прави реален HTTP. Всичко друго трябва да работи при
    изключена мрежа - това е проверявано от
    tests/test_no_network_in_suite.py и tests/test_netpolicy.py.
    """

    monkeypatch.setenv("AI_PROPERTY_ALLOW_NETWORK", "1")


# ============================================================
# TAG: MODULE FIXTURES
# ============================================================

@pytest.fixture(scope="session")
def normalizer():
    from tools import normalizer as mod
    return mod


@pytest.fixture(scope="session")
def matcher():
    from tools import matcher as mod
    return mod


@pytest.fixture(scope="session")
def deduplicator():
    from tools import deduplicator as mod
    return mod


@pytest.fixture(scope="session")
def consolidator():
    from tools import consolidator as mod
    return mod


@pytest.fixture(scope="session")
def finished_exporter():
    from tools import finished_exporter as mod
    return mod


@pytest.fixture(scope="session")
def resources():
    from tools import resources as mod
    return mod


# ============================================================
# TAG: ISOLATED PIPELINE TREE
# ============================================================

@pytest.fixture
def mini_raw_snapshot(tmp_path: Path, sample_geojson_path: Path) -> Path:
    """
    storage_raw/sofiaplan/<ts>/datasets/999/raw_data.geojson
    """

    ts = "01-01-2026_10"
    datasets_dir = tmp_path / "raw" / "sofiaplan" / ts / "datasets" / "999"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(
        sample_geojson_path,
        datasets_dir / "raw_data.geojson"
    )

    catalog = tmp_path / "raw" / "sofiaplan" / ts / "sofiaplan_datasets.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "id": 999,
                    "name": "test.sample",
                    "row_count": 6,
                    "size": 1
                }
            ],
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    return tmp_path / "raw" / "sofiaplan" / ts


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch):
    """
    Пренасочва всички проекционни папки на модулите
    към tmp_path, за да не се замърсява реалното storage/.
    """

    import tools.consolidator as cons
    import tools.deduplicator as dedup
    import tools.finished_exporter as fin
    import tools.matcher as mt
    import tools.normalizer as norm
    import tools.search_api as api

    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    patches = [
        (norm, "NORMALIZED_DIR", storage / "normalized"),
        (dedup, "DEDUPLICATED_DIR", storage / "deduplicated"),
        (cons, "CONSOLIDATED_DIR", storage / "consolidated"),
        (cons, "DEDUPLICATED_DIR", storage / "deduplicated"),
        (cons, "INDEX_DIR", storage / "indexes"),
        (cons, "MATCHED_DIR", storage / "matched"),
        (fin, "FINISHED_DIR", storage / "finished"),
        (fin, "ANALYTICS_DIR", storage / "analytics"),
        (mt, "INDEX_DIR", storage / "indexes"),
        (mt, "MATCHED_DIR", storage / "matched"),
        (api, "FINISHED_DIR", storage / "finished"),
        (api, "ANALYTICS_DIR", storage / "analytics"),
    ]

    for module, attr, value in patches:
        if hasattr(module, attr):
            monkeypatch.setattr(
                module,
                attr,
                value
            )

    fin._FINISHED_INDEX_CACHE.clear()

    yield storage

    fin._FINISHED_INDEX_CACHE.clear()
