# TAG: SCHEMA DISCOVERY V2.4
# Универсален инструмент за откриване на структурата
# на различни datasets.
#
# Основни цели:
# 1. Избира представителни datasets от каталога.
# 2. Използва Dataset Fetcher вместо собствен API код.
# 3. Анализира реалните RAW файлове.
# 4. Извършва Field Profiling.
# 5. Използва streaming при GeoJSON.
# 6. Не зарежда целия голям dataset в RAM.
# 7. Записва подробен Discovery Report.
#
# V2.1 НЕ променя RAW данните.


# TAG: STANDARD LIBRARY
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

# TAG: DATASET STATE MANAGER
from tools.dataset_state import (
    decide_discovery_action,
    save_analysis_cache,
    detect_file_format as detect_raw_file_format
)

# TAG: PROJECT ROOT
BASE_DIR = Path(__file__).resolve().parent.parent


# TAG: RAW STORAGE
RAW_DIR = BASE_DIR / "storage_raw"


# TAG: DISCOVERY STORAGE
DISCOVERY_DIR = BASE_DIR / "storage" / "discovery"


# TAG: CATALOG SOURCE
SOFIAPLAN_RAW_DIR = RAW_DIR / "sofiaplan"


# TAG: MAX CANDIDATES
MAX_CANDIDATES = 10


# TAG: SAMPLE SIZE
SAMPLE_SIZE = 5


# TAG: UNIQUE VALUE LIMIT
# Не пазим безкраен set при огромни datasets.
# След достигане на този лимит броенето се отбелязва
# като приблизително/ограничено.
UNIQUE_VALUE_LIMIT = 10000

# TAG: JSON SAFE CONVERSION
# Преобразува стойности, които стандартният JSON encoder
# на Python не може да сериализира директно.

def make_json_safe(value):

    # TAG: DECIMAL
    if isinstance(value, Decimal):
        return float(value)

    # TAG: PATH
    if isinstance(value, Path):
        return str(value)

    # TAG: DATETIME
    if isinstance(value, datetime):
        return value.isoformat()

    # TAG: DICTIONARY
    if isinstance(value, dict):
        return {
            str(key): make_json_safe(item)
            for key, item in value.items()
        }

    # TAG: LIST
    if isinstance(value, list):
        return [
            make_json_safe(item)
            for item in value
        ]

    # TAG: TUPLE
    if isinstance(value, tuple):
        return [
            make_json_safe(item)
            for item in value
        ]

    # TAG: JSON NATIVE TYPES
    if value is None or isinstance(
        value,
        (str, int, float, bool)
    ):
        return value

    # TAG: FALLBACK
    return str(value)

# TAG: SEMANTIC GROUPS
SEMANTIC_GROUPS = {

    "PROPERTY": [
        "сграда",
        "сгради",
        "жилище",
        "жилища",
        "жилищен",
        "жилищна",
        "жилищни",
        "имот",
        "имоти",
        "парцел",
        "парцели",
        "земя",
        "недвижим",
        "недвижими",
        "кадастър",
        "кадастрал",
        "строителство"
    ],

    "TRANSPORT": [
        "транспорт",
        "метро",
        "автобус",
        "автобуси",
        "трамвай",
        "трамваи",
        "тролей",
        "тролеи",
        "спирка",
        "спирки",
        "път",
        "пътища",
        "улица",
        "улици",
        "паркиране",
        "паркинг"
    ],

    "ENVIRONMENT": [
        "въздух",
        "шум",
        "наводнение",
        "наводнения",
        "замърсяване",
        "фпч",
        "екология",
        "околна среда",
        "растителност",
        "зелена система"
    ],

    "EDUCATION": [
        "училище",
        "училища",
        "детска градина",
        "детски градини",
        "образование"
    ],

    "CULTURE": [
        "археология",
        "културно наследство",
        "културни ценности",
        "музей",
        "история"
    ]
}


# TAG: TEXT NORMALIZATION
def normalize_text(value):
    if value is None:
        return ""

    return " ".join(
        str(value)
        .lower()
        .strip()
        .split()
    )


# TAG: FIND LATEST CATALOG
def find_latest_catalog():

    if not SOFIAPLAN_RAW_DIR.exists():
        return None

    catalogs = list(
        SOFIAPLAN_RAW_DIR.rglob(
            "sofiaplan_datasets.json"
        )
    )

    if not catalogs:
        return None

    catalogs.sort(
        key=lambda path: path.stat().st_mtime
    )

    return catalogs[-1]


# TAG: LOAD CATALOG
def load_catalog():

    catalog_file = find_latest_catalog()

    if catalog_file is None:
        raise FileNotFoundError(
            "SofiaPlan catalog was not found."
        )

    with catalog_file.open(
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "Catalog format is not a list."
        )

    return catalog_file, data


# TAG: SEMANTIC MATCH
def find_semantic_groups(dataset):

    text_parts = [
        normalize_text(dataset.get("name")),
        normalize_text(dataset.get("description")),
        normalize_text(dataset.get("category"))
    ]

    text = " ".join(
        part for part in text_parts
        if part
    )

    matches = []

    for group_name, keywords in SEMANTIC_GROUPS.items():

        for keyword in keywords:

            normalized_keyword = normalize_text(
                keyword
            )

            # TAG: PHRASE / TOKEN MATCH
            words = text.split()

            if " " in normalized_keyword:

                if normalized_keyword in text:
                    matches.append(group_name)
                    break

            else:

                if normalized_keyword in words:
                    matches.append(group_name)
                    break

    return matches


# TAG: DATASET SCORE
def dataset_selection_score(dataset):

    row_count = dataset.get(
        "row_count",
        0
    )

    try:
        row_count = int(row_count)
    except (
        TypeError,
        ValueError
    ):
        row_count = 0

    semantic_groups = find_semantic_groups(
        dataset
    )

    score = 0

    # TAG: SIZE DIVERSITY
    if row_count <= 10:
        score += 100

    elif row_count <= 1000:
        score += 80

    elif row_count <= 10000:
        score += 60

    elif row_count <= 100000:
        score += 40

    else:
        score += 20

    # TAG: SEMANTIC VALUE
    score += len(
        semantic_groups
    ) * 10

    # TAG: DESCRIPTION VALUE
    if dataset.get("description"):
        score += 5

    return score


# TAG: SELECT REPRESENTATIVE DATASETS
def select_candidates(catalog):

    if not catalog:
        return []

    selected = []

    used_ids = set()
    used_categories = set()
    used_groups = set()

    # TAG: SORT BY SIZE
    datasets_by_size = sorted(
        catalog,
        key=lambda item: int(
            item.get("row_count", 0) or 0
        )
        if str(
            item.get("row_count", 0) or 0
        ).isdigit()
        else 0
    )

    # TAG: SIZE REPRESENTATIVES
    size_indexes = [
        0,
        len(datasets_by_size) // 4,
        len(datasets_by_size) // 2,
        (len(datasets_by_size) * 3) // 4,
        len(datasets_by_size) - 1
    ]

    for index in size_indexes:

        if not datasets_by_size:
            continue

        dataset = datasets_by_size[
            max(
                0,
                min(
                    index,
                    len(datasets_by_size) - 1
                )
            )
        ]

        dataset_id = dataset.get("id")

        if dataset_id in used_ids:
            continue

        selected.append({
            "dataset": dataset,
            "reason": "size_representative"
        })

        used_ids.add(dataset_id)

    # TAG: CATEGORY DIVERSITY
    for dataset in sorted(
        catalog,
        key=dataset_selection_score,
        reverse=True
    ):

        if len(selected) >= MAX_CANDIDATES:
            break

        dataset_id = dataset.get("id")

        if dataset_id in used_ids:
            continue

        category = normalize_text(
            dataset.get("category")
        )

        if category and category in used_categories:
            continue

        selected.append({
            "dataset": dataset,
            "reason": "category_diversity"
        })

        used_ids.add(dataset_id)

        if category:
            used_categories.add(category)

    # TAG: SEMANTIC DIVERSITY
    for dataset in catalog:

        if len(selected) >= MAX_CANDIDATES:
            break

        dataset_id = dataset.get("id")

        if dataset_id in used_ids:
            continue

        groups = find_semantic_groups(
            dataset
        )

        new_groups = [
            group
            for group in groups
            if group not in used_groups
        ]

        if not new_groups:
            continue

        selected.append({
            "dataset": dataset,
            "reason": (
                "semantic_group:" +
                ",".join(new_groups)
            )
        })

        used_ids.add(dataset_id)

        for group in new_groups:
            used_groups.add(group)

    # TAG: FINAL FILL
    if len(selected) < MAX_CANDIDATES:

        for dataset in catalog:

            if len(selected) >= MAX_CANDIDATES:
                break

            dataset_id = dataset.get("id")

            if dataset_id in used_ids:
                continue

            selected.append({
                "dataset": dataset,
                "reason": "additional_dataset"
            })

            used_ids.add(dataset_id)

    return selected[:MAX_CANDIDATES]


# TAG: FIND EXISTING RAW FILE
def find_latest_raw_file(
    source,
    dataset_id
):

    dataset_root = (
        RAW_DIR /
        source
    )

    if not dataset_root.exists():
        return None

    candidates = []

    # TAG: RECURSIVE RAW SEARCH
    for file in dataset_root.rglob("*"):

        if not file.is_file():
            continue

        if not file.name.startswith(
            "raw_data"
        ):
            continue

        parts = file.parts

        if str(dataset_id) not in parts:
            continue

        candidates.append(file)

    if not candidates:
        return None

    candidates.sort(
        key=lambda path: path.stat().st_mtime
    )

    return candidates[-1]


# TAG: FETCH DATASET
def fetch_dataset(
    dataset_id,
    source="sofiaplan"
):

    print(
        f"       [FETCH] Dataset {dataset_id}"
    )

    try:

        # TAG: UNIVERSAL FETCHER
        from tools.dataset_fetcher import (
            download_dataset
        )

        raw_file = download_dataset(
            dataset_id,
            source
        )

        return raw_file

    except Exception as error:

        print(
            f"       [ERROR] {error}"
        )

        return None


# TAG: VALUE TYPE
def detect_value_type(value):

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, int):
        return "integer"

    if isinstance(value, float):
        return "float"

    if isinstance(value, str):
        return "string"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return type(value).__name__


# TAG: CREATE FIELD PROFILE
def create_field_profile():

    return {}


# TAG: UPDATE FIELD PROFILE
def update_field_profile(
    profiles,
    field_name,
    value
):

    if field_name not in profiles:

        profiles[field_name] = {
            "types": set(),
            "null_count": 0,
            "value_count": 0,
            "unique_values": set(),
            "unique_count_limited": False,
            "sample_values": []
        }

    profile = profiles[field_name]

    value_type = detect_value_type(
        value
    )

    profile["types"].add(
        value_type
    )

    profile["value_count"] += 1

    if value is None:

        profile["null_count"] += 1

        return

    # TAG: SAMPLE VALUES
    if len(
        profile["sample_values"]
    ) < SAMPLE_SIZE:

        if value not in profile[
            "sample_values"
        ]:

            try:

                profile[
                    "sample_values"
                ].append(value)

            except Exception:
                pass

    # TAG: UNIQUE VALUES
    if not profile[
        "unique_count_limited"
    ]:

        try:

            hash(value)

            profile[
                "unique_values"
            ].add(value)

        except TypeError:

            # Lists / dictionaries cannot
            # directly be stored in a set.
            try:

                serialized = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True
                )

                profile[
                    "unique_values"
                ].add(serialized)

            except Exception:
                pass

        if len(
            profile["unique_values"]
        ) >= UNIQUE_VALUE_LIMIT:

            profile[
                "unique_count_limited"
            ] = True


# TAG: FINALIZE FIELD PROFILES
def finalize_field_profiles(
    profiles,
    total_records
):

    result = {}

    for field_name, profile in profiles.items():

        null_count = profile[
            "null_count"
        ]

        if total_records > 0:

            null_percentage = (
                null_count /
                total_records
            ) * 100

        else:
            null_percentage = 0

        unique_count = len(
            profile[
                "unique_values"
            ]
        )

        result[field_name] = {

            "types": sorted(
                profile["types"]
            ),

            "null_count": null_count,

            "null_percentage": round(
                null_percentage,
                2
            ),

            "value_count": profile[
                "value_count"
            ],

            "unique_count": unique_count,

            "unique_count_limited":
                profile[
                    "unique_count_limited"
                ],

            "sample_values":
                profile[
                    "sample_values"
                ]
        }

    return result


# TAG: ANALYZE GEOJSON STREAMING
def analyze_geojson(
    file_path
):

    profiles = {}

    geometry_types = Counter()

    record_count = 0

    # TAG: STREAMING GEOJSON
    import ijson

    with file_path.open(
        "rb"
    ) as file:

        features = ijson.items(
            file,
            "features.item",
            use_float=True
        )

        for feature in features:

            record_count += 1

            geometry = feature.get(
                "geometry"
            )

            if isinstance(
                geometry,
                dict
            ):

                geometry_type = geometry.get(
                    "type"
                )

                if geometry_type:

                    geometry_types[
                        geometry_type
                    ] += 1

            properties = feature.get(
                "properties",
                {}
            )

            if not isinstance(
                properties,
                dict
            ):
                continue

            for field_name, value in properties.items():

                update_field_profile(
                    profiles,
                    field_name,
                    value
                )

    return {
        "format": "geojson",
        "records": record_count,
        "geometry_types": dict(
            geometry_types
        ),
        "fields": finalize_field_profiles(
            profiles,
            record_count
        )
    }


# TAG: ANALYZE JSON
def analyze_json(
    file_path
):

    with file_path.open(
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    profiles = {}

    record_count = 0

    if isinstance(
        data,
        list
    ):

        records = data

    elif isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get("records"),
            list
        ):

            records = data[
                "records"
            ]

        else:

            records = [
                data
            ]

    else:

        records = [
            {
                "value": data
            }
        ]

    for record in records:

        record_count += 1

        if not isinstance(
            record,
            dict
        ):
            continue

        for field_name, value in record.items():

            update_field_profile(
                profiles,
                field_name,
                value
            )

    return {
        "format": "json",
        "records": record_count,
        "geometry_types": {},
        "fields": finalize_field_profiles(
            profiles,
            record_count
        )
    }


# TAG: ANALYZE CSV
def analyze_csv(
    file_path
):

    profiles = {}

    record_count = 0

    with file_path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:

            record_count += 1

            for field_name, value in row.items():

                # TAG: CSV NULL
                if value == "":
                    value = None

                update_field_profile(
                    profiles,
                    field_name,
                    value
                )

    return {
        "format": "csv",
        "records": record_count,
        "geometry_types": {},
        "fields": finalize_field_profiles(
            profiles,
            record_count
        )
    }


# TAG: DETECT FORMAT
def detect_format(
    file_path
):
    """
    Използва централния Dataset State Manager
    за определяне на формата по extension или съдържание.
    """
    return detect_raw_file_format(
        file_path
    )


# TAG: ANALYZE FILE
def analyze_file(
    file_path
):

    detected_format = detect_format(
        file_path
    )

    if detected_format == "geojson":

        return analyze_geojson(
            file_path
        )

    if detected_format == "json":

        return analyze_json(
            file_path
        )

    if detected_format == "csv":

        return analyze_csv(
            file_path
        )

    raise ValueError(
        f"Unsupported format: {detected_format}"
    )


# TAG: CREATE OUTPUT DIRECTORY
def create_output_directory():

    # TAG: BULGARIA LOCAL TIME
    now = datetime.now(
        ZoneInfo("Europe/Sofia")
    )

    # TAG: SNAPSHOT TIMESTAMP
    timestamp = now.strftime(
        "%d-%m-%Y_%H"
    )

    output_dir = (
        DISCOVERY_DIR /
        timestamp
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return output_dir, now


# TAG: PRINT DATASET ANALYSIS
def print_analysis(
    dataset_id,
    analysis
):

    print()
    print(
        f"    Dataset: {dataset_id}"
    )

    print(
        f"    Format: "
        f"{analysis['format']}"
    )

    print(
        f"    Records: "
        f"{analysis['records']:,}"
    )

    if analysis[
        "geometry_types"
    ]:

        print(
            "    Geometry:"
        )

        for geometry_type, count in (
            analysis[
                "geometry_types"
            ].items()
        ):

            print(
                f"        "
                f"{geometry_type}: "
                f"{count:,}"
            )

    fields = analysis[
        "fields"
    ]

    print(
        f"    Fields: "
        f"{len(fields)}"
    )

    for field_name, profile in fields.items():

        types = ", ".join(
            profile["types"]
        )

        print(
            f"        "
            f"{field_name} -> "
            f"{types} | "
            f"null: "
            f"{profile['null_percentage']}% | "
            f"unique: "
            f"{profile['unique_count']}"
        )


# TAG: RUN DISCOVERY
def run_discovery():

    print()
    print("================================")
    print(" SCHEMA DISCOVERY V2.3")
    print("================================")
    print()

    # TAG: LOAD CATALOG
    try:

        catalog_file, catalog = (
            load_catalog()
        )

    except Exception as error:

        print(
            f"[ERROR] {error}"
        )

        return None

    print(
        f"[INFO] Catalog: "
        f"{catalog_file}"
    )

    print(
        f"[INFO] Catalog datasets: "
        f"{len(catalog)}"
    )

    # TAG: SELECT CANDIDATES
    candidates = select_candidates(
        catalog
    )

    print(
        f"[INFO] Candidates: "
        f"{len(candidates)}"
    )

    print()

    # TAG: OUTPUT DIRECTORY
    output_dir, now = (
        create_output_directory()
    )

    report = {

        "schema_version":
            "2.4",

        "generated_at":
            now.isoformat(),

        "catalog": {
            "file": str(
                catalog_file.relative_to(
                    BASE_DIR
                )
            ),
            "dataset_count":
                len(catalog)
        },

        "candidate_count":
            len(candidates),

        "analyzed_count":
            0,

        "failed_count":
            0,

        "candidates": [],

        "analyzed_datasets": [],

        "failed_datasets": []
    }

    # TAG: PROCESS CANDIDATES
    for index, candidate in enumerate(
        candidates,
        start=1
    ):

        dataset = candidate[
            "dataset"
        ]

        dataset_id = dataset.get(
            "id"
        )

        reason = candidate[
            "reason"
        ]

        print(
            f"[{index}/{len(candidates)}] "
            f"Dataset {dataset_id}"
        )

        print(
            f"       Reason: {reason}"
        )

        candidate_report = {

            "dataset_id":
                dataset_id,

            "name":
                dataset.get(
                    "name"
                ),

            "category":
                dataset.get(
                    "category"
                ),

            "provider":
                dataset.get(
                    "provider"
                ),

            "description":
                dataset.get(
                    "description"
                ),

            "relevant_at":
                dataset.get(
                    "relevant_at"
                ),

            "catalog_row_count":
                dataset.get(
                    "row_count"
                ),

            "catalog_size":
                dataset.get(
                    "size"
                ),

            "selection_reason":
                reason,

            "semantic_groups":
                find_semantic_groups(
                    dataset
                )
        }

        report[
            "candidates"
        ].append(
            candidate_report
        )

                # TAG: DATASET STATE
        decision = decide_discovery_action(
            "sofiaplan",
            dataset_id
        )

        action = decision["action"]
        state = decision["state"]


        # TAG: USE CACHE
        if action == "USE_CACHE":

            print(
                f"       [CACHE] "
                f"Known dataset - using cached analysis."
            )

            raw_file = (
                BASE_DIR /
                state["latest_file"]
            )

            analysis = decision[
                "cache"
            ]["analysis"]

            dataset_report = {
                "dataset_id":
                    dataset_id,

                "name":
                    dataset.get("name"),

                "category":
                    dataset.get("category"),

                "provider":
                    dataset.get("provider"),

                "selection_reason":
                    reason,

                "semantic_groups":
                    find_semantic_groups(
                        dataset
                    ),

                "raw_file":
                    state["latest_file"],

                "raw_file_size_bytes":
                    raw_file.stat().st_size,

                "dataset_state":
                    state,

                "processing":
                    "CACHE",

                "analysis":
                    analysis
            }

            report[
                "analyzed_datasets"
            ].append(
                dataset_report
            )

            report[
                "analyzed_count"
            ] += 1

            print_analysis(
                dataset_id,
                analysis
            )

            continue


        # TAG: UNKNOWN
        if action == "UNKNOWN":

            print(
                f"       [UNKNOWN] "
                f"Latest RAW format is unknown."
            )

            report[
                "failed_count"
            ] += 1

            report[
                "failed_datasets"
            ].append({
                "dataset_id":
                    dataset_id,

                "name":
                    dataset.get("name"),

                "error":
                    "Unknown RAW format.",

                "dataset_state":
                    state
            })

            continue


        # TAG: INVALID
        if action == "INVALID":

            print(
                f"       [INVALID] "
                f"Latest RAW snapshot is invalid."
            )

            report[
                "failed_count"
            ] += 1

            report[
                "failed_datasets"
            ].append({
                "dataset_id":
                    dataset_id,

                "name":
                    dataset.get("name"),

                "error":
                    "Invalid RAW snapshot.",

                "dataset_state":
                    state
            })

            continue


        # TAG: ANALYZE LOCAL
        if action == "ANALYZE_LOCAL":

            raw_file = (
                BASE_DIR /
                state["latest_file"]
            )

            print(
                f"       [RAW] Known local snapshot."
            )

            print(
                f"       [ANALYZE] New fingerprint - analyzing."
            )

            processing = "LOCAL"

        # TAG: FETCH
        elif action == "FETCH":

            raw_file = fetch_dataset(
                dataset_id,
                "sofiaplan"
            )

            if raw_file is None:
                report["failed_count"] += 1
                report["failed_datasets"].append({
                    "dataset_id": dataset_id,
                    "name": dataset.get("name"),
                    "error": "Dataset fetch failed.",
                    "dataset_state": state
                })
                continue

            print(
                f"       [FETCH] New RAW snapshot received."
            )

            # TAG: RECHECK STATE
            decision = decide_discovery_action(
                "sofiaplan",
                dataset_id
            )

            state = decision["state"]

            # TAG: USE FETCHED FILE
            # Ползваме директно върнатия файл от Fetcher.
            processing = "FETCH"

        else:
            report["failed_count"] += 1
            report["failed_datasets"].append({
                "dataset_id": dataset_id,
                "name": dataset.get("name"),
                "error": f"Unsupported discovery action: {action}",
                "dataset_state": state
            })
            continue

        # TAG: ANALYZE RAW
        try:
            analysis = analyze_file(raw_file)
        except Exception as error:
            report["failed_count"] += 1
            report["failed_datasets"].append({
                "dataset_id": dataset_id,
                "name": dataset.get("name"),
                "error": f"Analysis failed: {error}",
                "raw_file": str(raw_file.relative_to(BASE_DIR)),
                "dataset_state": state,
                "processing": processing
            })
            print(
                f"       [ERROR] Analysis failed: {error}"
            )
            continue

        # TAG: SAVE ANALYSIS CACHE
        fingerprint = state.get("latest_fingerprint")

        if fingerprint:
            try:
                cache_file = save_analysis_cache(
                    "sofiaplan",
                    dataset_id,
                    fingerprint,
                    analysis
                )
                print(
                    f"       [CACHE] Analysis saved."
                )
            except Exception as error:
                cache_file = None
                print(
                    f"       [WARN] Cache save failed: {error}"
                )
        else:
            cache_file = None

        # TAG: BUILD ANALYSIS REPORT
        dataset_report = {
            "dataset_id": dataset_id,
            "name": dataset.get("name"),
            "category": dataset.get("category"),
            "provider": dataset.get("provider"),
            "description": dataset.get("description"),
            "relevant_at": dataset.get("relevant_at"),
            "catalog_row_count": dataset.get("row_count"),
            "catalog_size": dataset.get("size"),
            "selection_reason": reason,
            "semantic_groups": find_semantic_groups(dataset),
            "raw_file": str(raw_file.relative_to(BASE_DIR)),
            "raw_file_size_bytes": raw_file.stat().st_size,
            "dataset_state": state,
            "processing": processing,
            "cache_file": (
                str(cache_file.relative_to(BASE_DIR))
                if cache_file
                else None
            ),
            "analysis": analysis
        }

        report["analyzed_datasets"].append(dataset_report)
        report["analyzed_count"] += 1

        print_analysis(
            dataset_id,
            analysis
        )

    # TAG: SAVE REPORT
    output_file = (
        output_dir /
        "schema_discovery_v2_4.json"
    )

    # TAG: MAKE REPORT JSON SAFE
    safe_report = make_json_safe(
    report
    )


    # TAG: SAVE DISCOVERY REPORT
    output_file.write_text(
    json.dumps(
        safe_report,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
    )

    # TAG: FINAL RESULT
    print()
    print("================================")
    print(" SCHEMA DISCOVERY V2.4 COMPLETED")
    print("================================")
    print()

    print(
        f"Catalog datasets: "
        f"{len(catalog)}"
    )

    print(
        f"Candidates: "
        f"{len(candidates)}"
    )

    print(
        f"Analyzed: "
        f"{report['analyzed_count']}"
    )

    print(
        f"Failed: "
        f"{report['failed_count']}"
    )

    print()
    print(
        f"Report: "
        f"{output_file}"
    )

    return output_file


# TAG: PROGRAM START
if __name__ == "__main__":

    run_discovery()
