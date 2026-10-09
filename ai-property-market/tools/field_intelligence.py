# TAG: FIELD INTELLIGENCE V1.4
# Анализира смисъла на полетата от Schema Discovery.
# V1.4 добавя предложения за неизвестни полета,
# без да ги класифицира автоматично като сигурни.

import json
import re
from pathlib import Path


# TAG: PROJECT ROOT
BASE_DIR = Path(__file__).resolve().parent.parent


# TAG: DISCOVERY STORAGE
DISCOVERY_DIR = (
    BASE_DIR /
    "storage" /
    "discovery"
)


# TAG: FIELD DICTIONARY
FIELD_DICTIONARY = {

    "id": [
        "id",
        "objectid",
        "object_id",
        "identifier",
        "record_id"
    ],

    "name": [
        "name",
        "name_bg",
        "name_lat",
        "title",
        "име",
        "наименование"
    ],

    "type": [
        "type",
        "тип",
        "вид",
        "category"
    ],

    "area": [
        "area",
        "area_m2",
        "area_m",
        "area_kv_m",
        "sqm",
        "square_meters",
        "plosht",
        "plosht_m2",
        "площ"
    ],

    "price": [
        "price",
        "price_eur",
        "price_bgn",
        "cena",
        "цена"
    ],

    "address": [
        "address",
        "adres",
        "адрес",
        "location"
    ],

    "floor": [
        "floor",
        "etaj",
        "етаж"
    ],

    "rooms": [
        "rooms",
        "room",
        "stai",
        "стаи"
    ],

    "year": [
        "year",
        "year_built",
        "godina",
        "година"
    ],

    "length": [
        "length",
        "dlazhina",
        "дължина"
    ],

    "width": [
        "width",
        "shirina",
        "ширина"
    ],

    "direction": [
        "direction",
        "posoka",
        "посока"
    ],

    "condition": [
        "condition",
        "cond",
        "sastoyanie",
        "състояние"
    ],

    "notes": [
        "notes",
        "note",
        "zabelezhka",
        "zabelezhki",
        "бележка",
        "бележки"
    ],

    "district": [
        "district",
        "kvartal",
        "rajon",
        "квартал",
        "район"
    ],

    "parcel": [
        "parcel",
        "parцel",
        "парцел"
    ],

    "source": [
        "source",
        "iztochnik",
        "източник"
    ],

    "date": [
        "date",
        "date_",
        "data",
        "дата"
    ],

    "url": [
        "url",
        "link",
        "iztochnik_url",
        "връзка"
    ],

    "layer": [
        "layer",
        "sloj",
        "слой"
    ],

    "legend": [
        "legend",
        "legenda",
        "легенда"
    ],

    "document": [
        "document",
        "dokument",
        "документ"
    ],

    "code": [
        "code",
        "sitecode",
        "iba_code",
        "akb_n",
        "код"
    ]
}


# TAG: CONTEXT KEYWORDS
# Думи, които могат да помогнат при анализ на неизвестно поле.

CONTEXT_KEYWORDS = {

    "area": [
        "area",
        "plosht",
        "площ",
        "ha",
        "hectare",
        "hectares"
    ],

    "length": [
        "length",
        "dlazhina",
        "дължина"
    ],

    "width": [
        "width",
        "shirina",
        "ширина"
    ],

    "percentage": [
        "perc",
        "percent",
        "percentage",
        "процент",
        "%"
    ],

    "identifier": [
        "id",
        "ident",
        "identifier",
        "objectid",
        "facilityid",
        "code"
    ],

    "type": [
        "type",
        "typ",
        "тип",
        "вид"
    ],

    "notes": [
        "note",
        "notes",
        "memo",
        "zabelezhka",
        "бележка"
    ],

    "point": [
        "point",
        "punkt",
        "точка"
    ],

    "break": [
        "break",
        "frombreak",
        "tobreak"
    ]
}


# TAG: NORMALIZE FIELD NAME
def normalize_field_name(name):
    text = str(name).strip().lower()

    text = text.replace("-", "_")
    text = text.replace(" ", "_")

    text = re.sub(
        r"_+",
        "_",
        text
    )

    return text


# TAG: EXACT MATCH
def exact_match(field_name):
    normalized = normalize_field_name(
        field_name
    )

    for semantic_name, candidates in FIELD_DICTIONARY.items():

        normalized_candidates = [
            normalize_field_name(candidate)
            for candidate in candidates
        ]

        if normalized in normalized_candidates:
            return {
                "semantic_field": semantic_name,
                "match_type": "exact",
                "confidence": 100,
                "reason": "exact_field_name_match"
            }

    return None


# TAG: NAME SUGGESTION
def name_suggestions(field_name):
    normalized = normalize_field_name(
        field_name
    )

    suggestions = []

    for semantic_name, keywords in CONTEXT_KEYWORDS.items():

        score = 0
        matched_keywords = []

        for keyword in keywords:

            normalized_keyword = normalize_field_name(
                keyword
            )

            if normalized_keyword in normalized:

                score += 1

                matched_keywords.append(
                    normalized_keyword
                )

        if score > 0:

            suggestions.append({
                "semantic_field": semantic_name,
                "score": score,
                "matched_keywords": matched_keywords
            })

    suggestions.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return suggestions


# TAG: VALUE TYPE HINT
def value_type_hint(source_types):

    normalized_types = [
        str(value).lower()
        for value in source_types
    ]

    numeric_types = {
        "integer",
        "float",
        "decimal",
        "number"
    }

    if any(
        value in numeric_types
        for value in normalized_types
    ):
        return "numeric"

    if "string" in normalized_types:
        return "string"

    return "other"


# TAG: BUILD SUGGESTIONS
def build_suggestions(
    field_name,
    field_info
):

    suggestions = name_suggestions(
        field_name
    )

    if not suggestions:
        return []

    value_hint = value_type_hint(
        field_info.get(
            "types",
            []
        )
    )

    results = []

    for suggestion in suggestions:

        semantic_field = suggestion[
            "semantic_field"
        ]

        score = suggestion["score"]

        confidence = 50

        # TAG: NUMERIC AREA
        if (
            semantic_field == "area"
            and value_hint == "numeric"
        ):
            confidence += 20

        # TAG: PERCENTAGE
        if (
            semantic_field == "percentage"
            and value_hint == "numeric"
        ):
            confidence += 20

        # TAG: LENGTH
        if (
            semantic_field == "length"
            and value_hint == "numeric"
        ):
            confidence += 15

        # TAG: WIDTH
        if (
            semantic_field == "width"
            and value_hint == "numeric"
        ):
            confidence += 15

        # TAG: IDENTIFIER
        if semantic_field == "identifier":
            confidence += 5

        confidence = min(
            confidence,
            95
        )

        results.append({
            "semantic_field": semantic_field,
            "confidence": confidence,
            "matched_keywords": suggestion[
                "matched_keywords"
            ],
            "value_type": value_hint
        })

    return results


# TAG: ANALYZE FIELD
def analyze_field(
    field_name,
    field_info
):

    exact = exact_match(
        field_name
    )

    if exact is not None:

        return {
            "source_field": field_name,
            "source_types": field_info.get(
                "types",
                []
            ),
            "null_percentage": field_info.get(
                "null_percentage"
            ),
            "unique_count": field_info.get(
                "unique_count"
            ),
            **exact
        }

    suggestions = build_suggestions(
        field_name,
        field_info
    )

    result = {
        "source_field": field_name,
        "source_types": field_info.get(
            "types",
            []
        ),
        "null_percentage": field_info.get(
            "null_percentage"
        ),
        "unique_count": field_info.get(
            "unique_count"
        ),
        "semantic_field": None,
        "match_type": "unknown",
        "confidence": 0
    }

    # TAG: ADD SUGGESTIONS
    if suggestions:

        result["suggestions"] = suggestions

    return result


# TAG: ANALYZE DATASET
def analyze_dataset(dataset):

    analysis = dataset.get(
        "analysis",
        {}
    )

    fields = analysis.get(
        "fields",
        {}
    )

    results = []

    for field_name, field_info in fields.items():

        results.append(
            analyze_field(
                field_name,
                field_info
            )
        )

    return results


# TAG: FIND LATEST REPORT
def find_latest_report():

    reports = list(
        DISCOVERY_DIR.rglob(
            "schema_discovery_v2_*.json"
        )
    )

    if not reports:
        return None

    reports.sort(
        key=lambda path: path.stat().st_mtime
    )

    return reports[-1]


# TAG: LOAD REPORT
def load_discovery_report(
    report_file
):

    with report_file.open(
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# TAG: BUILD REPORT
def build_report(
    discovery_report
):

    datasets = discovery_report.get(
        "analyzed_datasets",
        []
    )

    analyzed_datasets = []

    for dataset in datasets:

        dataset_id = dataset.get(
            "dataset_id"
        )

        field_matches = analyze_dataset(
            dataset
        )

        analyzed_datasets.append({
            "dataset_id": dataset_id,
            "name": dataset.get(
                "name"
            ),
            "category": dataset.get(
                "category"
            ),
            "fields": field_matches
        })

    return {
        "schema_version": "1.4",
        "source_schema_version":
            discovery_report.get(
                "schema_version"
            ),
        "datasets_analyzed":
            len(analyzed_datasets),
        "datasets":
            analyzed_datasets
    }


# TAG: SAVE REPORT
def save_report(report):

    output_dir = (
        DISCOVERY_DIR /
        "field_intelligence"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_dir /
        "field_intelligence.json"
    )

    output_file.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return output_file


# TAG: MAIN
def main():

    print(
        "================================"
    )

    print(
        " FIELD INTELLIGENCE V1.4"
    )

    print(
        "================================"
    )

    print()

    # TAG: FIND REPORT
    report_file = find_latest_report()

    if report_file is None:

        print(
            "[ERROR] No Schema Discovery report found."
        )

        return

    print(
        f"[INFO] Discovery report: "
        f"{report_file}"
    )

    # TAG: LOAD
    discovery_report = (
        load_discovery_report(
            report_file
        )
    )

    # TAG: ANALYZE
    intelligence_report = (
        build_report(
            discovery_report
        )
    )

    # TAG: SAVE
    output_file = save_report(
        intelligence_report
    )

    # TAG: STATISTICS
    total_fields = sum(
        len(dataset["fields"])
        for dataset
        in intelligence_report["datasets"]
    )

    known_fields = sum(
        1
        for dataset
        in intelligence_report["datasets"]
        for field
        in dataset["fields"]
        if field["semantic_field"]
        is not None
    )

    suggested_fields = sum(
        1
        for dataset
        in intelligence_report["datasets"]
        for field
        in dataset["fields"]
        if field.get("suggestions")
    )

    unknown_fields = (
        total_fields -
        known_fields
    )

    print()

    print(
        "[OK] Field Intelligence completed."
    )

    print(
        f"Datasets analyzed: "
        f"{intelligence_report['datasets_analyzed']}"
    )

    print(
        f"Fields detected: "
        f"{total_fields}"
    )

    print(
        f"Known fields: "
        f"{known_fields}"
    )

    print(
        f"Unknown fields: "
        f"{unknown_fields}"
    )

    print(
        f"Fields with suggestions: "
        f"{suggested_fields}"
    )

    print()

    print(
        f"Report: {output_file}"
    )


# TAG: PROGRAM START
if __name__ == "__main__":
    main()