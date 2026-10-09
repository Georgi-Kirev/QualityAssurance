# TAG: DATASET STATE MANAGER V1.1
# Универсален инструмент за определяне състоянието
# на локалните RAW dataset snapshots.
#
# V1.1 подобрения:
# 1. Безопасен JSON cache при Decimal/Path/datetime.
# 2. Разпознаване на неизвестни разширения по съдържание.
# 3. SHA-256 fingerprint без зареждане на целия файл в RAM.
# 4. Запазва старите RAW snapshots без промяна.

# TAG: STANDARD LIBRARY
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

# TAG: PROJECT ROOT
BASE_DIR = Path(__file__).resolve().parent.parent

# TAG: RAW STORAGE
RAW_DIR = BASE_DIR / "storage_raw"

# TAG: DISCOVERY CACHE
CACHE_DIR = BASE_DIR / "storage" / "discovery" / "cache"

# TAG: SNAPSHOT AGE
MAX_SNAPSHOT_AGE_HOURS = 24

# TAG: KNOWN FORMATS
KNOWN_FORMATS = {
    ".geojson": "geojson",
    ".json": "json",
    ".csv": "csv",
}


# TAG: BULGARIA TIME
def now_local():
    return datetime.now(ZoneInfo("Europe/Sofia"))


# TAG: JSON SAFE CONVERSION
def make_json_safe(value):
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): make_json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [make_json_safe(item) for item in value]

    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]

    if isinstance(value, set):
        return [make_json_safe(item) for item in value]

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


# TAG: FIND RAW FILES
def find_raw_files(source, dataset_id):
    source_root = RAW_DIR / source

    if not source_root.exists():
        return []

    files = []

    for file in source_root.rglob("raw_data*"):
        if not file.is_file():
            continue

        if str(dataset_id) not in file.parts:
            continue

        files.append(file)

    files.sort(key=lambda path: path.stat().st_mtime)
    return files


# TAG: FIND LATEST RAW
def find_latest_raw(source, dataset_id):
    files = find_raw_files(source, dataset_id)

    if not files:
        return None

    return files[-1]


# TAG: DETECT FILE FORMAT
# Първо използваме extension. При .bin/unknown проверяваме съдържанието.
def detect_file_format(file_path):
    extension = file_path.suffix.lower()

    if extension in KNOWN_FORMATS:
        return KNOWN_FORMATS[extension]

    # TAG: CONTENT DETECTION
    try:
        with file_path.open("rb") as file:
            sample = file.read(1024 * 1024)

        text = sample.decode("utf-8-sig").lstrip()

        if not text:
            return "unknown"

        # CSV heuristic for legacy text files.
        first_line = text.splitlines()[0] if text.splitlines() else ""

        try:
            parsed = json.loads(text)

            if isinstance(parsed, dict):
                if parsed.get("type") == "FeatureCollection":
                    return "geojson"

                if "features" in parsed:
                    return "geojson"

                return "json"

            if isinstance(parsed, list):
                return "json"

        except Exception:
            pass

        if "," in first_line or ";" in first_line or "\t" in first_line:
            return "csv"

        if text:
            return "txt"

    except Exception:
        return "unknown"

    return "unknown"


# TAG: CALCULATE FINGERPRINT
def calculate_fingerprint(file_path, chunk_size=1024 * 1024):
    sha256 = hashlib.sha256()

    with file_path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)

            if not chunk:
                break

            sha256.update(chunk)

    return sha256.hexdigest()


# TAG: CACHE PATH
def get_cache_path(source, dataset_id, fingerprint):
    dataset_cache_dir = (
        CACHE_DIR /
        source /
        str(dataset_id)
    )

    dataset_cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return dataset_cache_dir / f"{fingerprint}.json"


# TAG: LOAD CACHE
def load_analysis_cache(source, dataset_id, fingerprint):
    cache_file = get_cache_path(
        source,
        dataset_id,
        fingerprint
    )

    if not cache_file.exists():
        return None

    try:
        with cache_file.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return None


# TAG: SAVE CACHE
def save_analysis_cache(source, dataset_id, fingerprint, analysis):
    cache_file = get_cache_path(
        source,
        dataset_id,
        fingerprint
    )

    payload = {
        "source": source,
        "dataset_id": dataset_id,
        "fingerprint": fingerprint,
        "cached_at": now_local().isoformat(),
        "analysis": make_json_safe(analysis)
    }

    cache_file.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return cache_file


# TAG: CHECK SNAPSHOT AGE
def get_snapshot_age_hours(file_path):
    modified_timestamp = file_path.stat().st_mtime

    modified_at = datetime.fromtimestamp(
        modified_timestamp,
        tz=ZoneInfo("Europe/Sofia")
    )

    age = now_local() - modified_at

    return age.total_seconds() / 3600


# TAG: SNAPSHOT STATUS
def get_snapshot_status(file_path):
    if file_path is None:
        return {
            "status": "MISSING",
            "format": None,
            "fingerprint": None,
            "age_hours": None
        }

    detected_format = detect_file_format(file_path)
    age_hours = get_snapshot_age_hours(file_path)

    if detected_format == "unknown":
        return {
            "status": "UNKNOWN",
            "format": "unknown",
            "fingerprint": None,
            "age_hours": round(age_hours, 2)
        }

    try:
        fingerprint = calculate_fingerprint(file_path)
    except Exception:
        return {
            "status": "INVALID",
            "format": detected_format,
            "fingerprint": None,
            "age_hours": round(age_hours, 2)
        }

    if age_hours <= MAX_SNAPSHOT_AGE_HOURS:
        status = "LATEST"
    else:
        status = "OLD"

    return {
        "status": status,
        "format": detected_format,
        "fingerprint": fingerprint,
        "age_hours": round(age_hours, 2)
    }


# TAG: DATASET STATE
def inspect_dataset_state(source, dataset_id):
    raw_files = find_raw_files(source, dataset_id)

    if not raw_files:
        return {
            "source": source,
            "dataset_id": dataset_id,
            "status": "MISSING",
            "latest_file": None,
            "latest_format": None,
            "latest_fingerprint": None,
            "latest_age_hours": None,
            "snapshot_count": 0,
            "snapshots": []
        }

    snapshots = []

    for file in raw_files:
        status = get_snapshot_status(file)

        snapshots.append({
            "file": str(file.relative_to(BASE_DIR)),
            "filename": file.name,
            "modified_at": datetime.fromtimestamp(
                file.stat().st_mtime,
                tz=ZoneInfo("Europe/Sofia")
            ).isoformat(),
            **status
        })

    latest_file = raw_files[-1]
    latest_status = get_snapshot_status(latest_file)

    return {
        "source": source,
        "dataset_id": dataset_id,
        "status": latest_status["status"],
        "latest_file": str(latest_file.relative_to(BASE_DIR)),
        "latest_format": latest_status["format"],
        "latest_fingerprint": latest_status["fingerprint"],
        "latest_age_hours": latest_status["age_hours"],
        "snapshot_count": len(raw_files),
        "snapshots": snapshots
    }


# TAG: DISCOVERY DECISION
def decide_discovery_action(source, dataset_id):
    state = inspect_dataset_state(source, dataset_id)
    status = state["status"]

    # TAG: NO RAW
    if status == "MISSING":
        return {
            "action": "FETCH",
            "reason": "No local RAW snapshot.",
            "state": state,
            "cache": None
        }

    # TAG: UNKNOWN FORMAT
    if status == "UNKNOWN":
        return {
            "action": "UNKNOWN",
            "reason": "Latest RAW file format could not be determined.",
            "state": state,
            "cache": None
        }

    # TAG: INVALID
    if status == "INVALID":
        return {
            "action": "INVALID",
            "reason": "Latest RAW file could not be fingerprinted.",
            "state": state,
            "cache": None
        }

    fingerprint = state["latest_fingerprint"]

    # TAG: CACHE LOOKUP
    cache = load_analysis_cache(
        source,
        dataset_id,
        fingerprint
    )

    if cache is not None:
        return {
            "action": "USE_CACHE",
            "reason": "Known fingerprint with existing analysis cache.",
            "state": state,
            "cache": cache
        }

    # TAG: LOCAL ANALYSIS
    return {
        "action": "ANALYZE_LOCAL",
        "reason": "Local RAW snapshot is available but has no analysis cache.",
        "state": state,
        "cache": None
    }
