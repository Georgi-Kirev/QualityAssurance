# TAG: DATASET INSPECTOR
# Универсален инструмент за анализ на RAW dataset файлове.
#
# Поддържани режими:
#
# 1. Конкретен dataset:
#    python tools/dataset_inspector.py sofiaplan 626
#
# 2. Всички dataset-и:
#    python tools/dataset_inspector.py --all


import csv
import json
import sys
from pathlib import Path


# ============================================================
# TAG: PROJECT ROOT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# TAG: RAW STORAGE
# ============================================================

RAW_DIR = BASE_DIR / "storage_raw"


# ============================================================
# TAG: FORMAT DETECTION
# ============================================================

def detect_format(file_path: Path) -> str:

    extension = file_path.suffix.lower()

    if extension == ".geojson":
        return "geojson"

    if extension == ".json":
        return "json"

    if extension == ".csv":
        return "csv"

    if extension == ".txt":
        return "text"

    if extension == ".bin":
        return "unknown"

    return extension.replace(".", "") or "unknown"


# ============================================================
# TAG: VALUE TYPE DETECTION
# ============================================================

def detect_value_type(value):

    if value is None:
        return "null"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, int):
        return "integer"

    if isinstance(value, float):
        return "float"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return "string"


# ============================================================
# TAG: GEOJSON INSPECTION
# ============================================================

def inspect_geojson(file_path: Path):

    with file_path.open(
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    features = data.get(
        "features",
        []
    )

    geometry_types = {}
    property_types = {}

    for feature in features:

        # TAG: GEOMETRY
        geometry = feature.get(
            "geometry"
        )

        if geometry:

            geometry_type = geometry.get(
                "type",
                "unknown"
            )

            geometry_types[
                geometry_type
            ] = geometry_types.get(
                geometry_type,
                0
            ) + 1

        # TAG: PROPERTIES
        properties = feature.get(
            "properties",
            {}
        )

        for key, value in properties.items():

            value_type = detect_value_type(
                value
            )

            if key not in property_types:

                property_types[key] = set()

            property_types[key].add(
                value_type
            )

    return {
        "format": "geojson",
        "records": len(features),
        "geometry_types": geometry_types,
        "property_types": property_types
    }


# ============================================================
# TAG: JSON INSPECTION
# ============================================================

def inspect_json(file_path: Path):

    with file_path.open(
        "r",
        encoding="utf-8"
    ) as file:

        data = json.load(file)

    # TAG: GEOJSON
    if (
        isinstance(data, dict)
        and data.get("type") == "FeatureCollection"
    ):

        return inspect_geojson(
            file_path
        )

    # TAG: OBJECT
    if isinstance(data, dict):

        fields = {}

        for key, value in data.items():

            fields[key] = detect_value_type(
                value
            )

        return {
            "format": "json",
            "records": 1,
            "root_type": "object",
            "fields": fields
        }

    # TAG: ARRAY
    if isinstance(data, list):

        fields = {}

        for item in data:

            if isinstance(item, dict):

                for key, value in item.items():

                    value_type = detect_value_type(
                        value
                    )

                    if key not in fields:
                        fields[key] = set()

                    fields[key].add(
                        value_type
                    )

        return {
            "format": "json",
            "records": len(data),
            "root_type": "array",
            "fields": fields
        }

    return {
        "format": "json",
        "records": 1,
        "root_type": detect_value_type(data)
    }


# ============================================================
# TAG: CSV INSPECTION
# ============================================================

def inspect_csv(file_path: Path):

    with file_path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        fields = reader.fieldnames or []

        row_count = 0

        for _ in reader:
            row_count += 1

    return {
        "format": "csv",
        "records": row_count,
        "fields": fields
    }


# ============================================================
# TAG: FILE INSPECTION
# ============================================================

def inspect_file(file_path: Path):

    detected_format = detect_format(
        file_path
    )

    result = {
        "file": file_path,
        "format": detected_format,
        "size": file_path.stat().st_size
    }

    try:

        if detected_format in (
            "geojson",
            "json"
        ):

            result.update(
                inspect_json(
                    file_path
                )
            )

        elif detected_format == "csv":

            result.update(
                inspect_csv(
                    file_path
                )
            )

    except Exception as error:

        result["error"] = str(error)

    return result


# ============================================================
# TAG: FIND DATASET SNAPSHOTS
# ============================================================

def find_dataset_snapshots(
    source: str,
    dataset_id: str
):

    source_dir = (
        RAW_DIR /
        source
    )

    if not source_dir.exists():
        return []

    snapshots = []

    # TAG: DATE DIRECTORIES
    date_dirs = [
        path
        for path in source_dir.iterdir()
        if path.is_dir()
    ]

    # TAG: SORT OLDEST TO NEWEST
    date_dirs.sort(
        key=lambda path: path.stat().st_mtime
    )

    # TAG: SEARCH SNAPSHOTS
    for date_dir in date_dirs:

        dataset_dir = (
            date_dir /
            "datasets" /
            dataset_id
        )

        if not dataset_dir.exists():
            continue

        raw_files = [
            file
            for file in dataset_dir.iterdir()
            if file.is_file()
            and file.name.startswith("raw_data")
        ]

        for raw_file in raw_files:

            snapshots.append({
                "source": source,
                "dataset_id": dataset_id,
                "date": date_dir.name,
                "file": raw_file,
                "modified": raw_file.stat().st_mtime
            })

    # TAG: SORT BY DATE
    snapshots.sort(
        key=lambda item: item["modified"]
    )

    return snapshots


# ============================================================
# TAG: FIND ALL DATASET GROUPS
# ============================================================

def find_all_dataset_groups():

    groups = {}

    if not RAW_DIR.exists():
        return groups

    # TAG: SOURCES
    source_dirs = [
        path
        for path in RAW_DIR.iterdir()
        if path.is_dir()
    ]

    for source_dir in source_dirs:

        source = source_dir.name

        # TAG: DATE DIRECTORIES
        date_dirs = [
            path
            for path in source_dir.iterdir()
            if path.is_dir()
        ]

        for date_dir in date_dirs:

            datasets_dir = (
                date_dir /
                "datasets"
            )

            if not datasets_dir.exists():
                continue

            # TAG: DATASET DIRECTORIES
            dataset_dirs = [
                path
                for path in datasets_dir.iterdir()
                if path.is_dir()
            ]

            for dataset_dir in dataset_dirs:

                dataset_id = dataset_dir.name

                key = (
                    source,
                    dataset_id
                )

                if key not in groups:

                    groups[key] = []

                # TAG: RAW FILES
                raw_files = [
                    file
                    for file in dataset_dir.iterdir()
                    if file.is_file()
                    and file.name.startswith("raw_data")
                ]

                for raw_file in raw_files:

                    groups[key].append({
                        "source": source,
                        "dataset_id": dataset_id,
                        "date": date_dir.name,
                        "file": raw_file,
                        "modified": raw_file.stat().st_mtime
                    })

    # TAG: SORT SNAPSHOTS
    for key in groups:

        groups[key].sort(
            key=lambda item: item["modified"]
        )

    return groups


# ============================================================
# TAG: FORMAT SIZE
# ============================================================

def format_size(size_bytes: int) -> str:

    if size_bytes >= 1024 * 1024:

        return f"{size_bytes / (1024 * 1024):.2f} MB"

    if size_bytes >= 1024:

        return f"{size_bytes / 1024:.2f} KB"

    return f"{size_bytes} B"


# ============================================================
# TAG: PRINT ALL
# ============================================================

def inspect_all():

    print()
    print("================================")
    print(" DATASET INSPECTOR - ALL")
    print("================================")
    print()

    groups = find_all_dataset_groups()

    if not groups:

        print(
            "[WARNING] No RAW datasets found."
        )

        return

    print(
        f"[INFO] Unique datasets found: "
        f"{len(groups)}"
    )

    print()

    # TAG: HEADER
    print(
        f"{'SOURCE':<12}"
        f"{'ID':<8}"
        f"{'SNAPSHOTS':<12}"
        f"{'LATEST':<14}"
        f"{'FORMAT':<10}"
        f"{'SIZE':<12}"
        f"{'RECORDS':<10}"
        f"{'GEOMETRY'}"
    )

    print("-" * 105)

    # TAG: SUMMARY
    format_counts = {}
    total_records = 0

    # TAG: DATASET LOOP
    for (source, dataset_id), snapshots in groups.items():

        if not snapshots:
            continue

        # TAG: LATEST SNAPSHOT
        latest = snapshots[-1]

        result = inspect_file(
            latest["file"]
        )

        detected_format = result.get(
            "format",
            "unknown"
        )

        size = format_size(
            result.get(
                "size",
                0
            )
        )

        records = result.get(
            "records",
            "-"
        )

        # TAG: GEOMETRY
        geometry_types = result.get(
            "geometry_types",
            {}
        )

        if geometry_types:

            geometry_text = ", ".join(
                f"{name} ({count})"
                for name, count
                in geometry_types.items()
            )

        else:

            geometry_text = "-"

        # TAG: PRINT
        print(
            f"{source:<12}"
            f"{dataset_id:<8}"
            f"{len(snapshots):<12}"
            f"{latest['date']:<14}"
            f"{detected_format:<10}"
            f"{size:<12}"
            f"{str(records):<10}"
            f"{geometry_text}"
        )

        # TAG: FORMAT COUNT
        format_counts[
            detected_format
        ] = format_counts.get(
            detected_format,
            0
        ) + 1

        # TAG: RECORD COUNT
        if isinstance(records, int):

            total_records += records

    # ========================================================
    # TAG: SUMMARY
    # ========================================================

    print()
    print("================================")
    print(" SUMMARY")
    print("================================")
    print()

    print(
        f"Unique datasets: {len(groups)}"
    )

    print(
        f"Total latest records: "
        f"{total_records:,}"
    )

    print()

    print("FORMATS:")

    for format_name, count in sorted(
        format_counts.items()
    ):

        print(
            f"    {format_name:<10} {count}"
        )

    # ========================================================
    # TAG: SNAPSHOT DETAILS
    # ========================================================

    print()
    print("SNAPSHOT HISTORY:")
    print()

    for (source, dataset_id), snapshots in groups.items():

        print(
            f"{source} / Dataset {dataset_id}"
        )

        for index, snapshot in enumerate(
            snapshots
        ):

            if index == len(snapshots) - 1:
                marker = "LATEST"
            else:
                marker = "OLD"

            file_path = snapshot["file"]

            file_format = detect_format(
                file_path
            )

            print(
                f"    [{marker:<6}] "
                f"{snapshot['date']} | "
                f"{file_format:<8} | "
                f"{file_path.name}"
            )

        print()

    print(
        "[OK] Inspection completed."
    )


# ============================================================
# TAG: SINGLE DATASET
# ============================================================

def inspect_single(
    source: str,
    dataset_id: str
):

    snapshots = find_dataset_snapshots(
        source,
        dataset_id
    )

    if not snapshots:

        print()
        print(
            "[ERROR] Dataset not found."
        )

        return

    print()
    print("================================")
    print(" DATASET INSPECTOR")
    print("================================")
    print()

    print(
        f"Source: {source}"
    )

    print(
        f"Dataset ID: {dataset_id}"
    )

    print(
        f"Snapshots: {len(snapshots)}"
    )

    print()

    # TAG: SNAPSHOT HISTORY
    print("SNAPSHOT HISTORY:")
    print()

    for index, snapshot in enumerate(
        snapshots
    ):

        file_path = snapshot["file"]

        file_format = detect_format(
            file_path
        )

        if index == len(snapshots) - 1:
            marker = "LATEST"
        else:
            marker = "OLD"

        print(
            f"    [{marker:<6}] "
            f"{snapshot['date']} | "
            f"{file_format:<8} | "
            f"{format_size(file_path.stat().st_size):<12} | "
            f"{file_path.name}"
        )

    # TAG: LATEST
    latest = snapshots[-1]

    print()
    print("================================")
    print(" LATEST SNAPSHOT")
    print("================================")
    print()

    print(
        f"File: {latest['file']}"
    )

    print(
        f"Format: "
        f"{detect_format(latest['file'])}"
    )

    print(
        f"Size: "
        f"{format_size(latest['file'].stat().st_size)}"
    )

    print()

    # TAG: INSPECT
    result = inspect_file(
        latest["file"]
    )

    if "records" in result:

        print(
            f"Records: "
            f"{result['records']:,}"
        )

    # TAG: GEOMETRY
    geometry_types = result.get(
        "geometry_types",
        {}
    )

    if geometry_types:

        print()
        print("GEOMETRY TYPES:")

        for geometry_type, count in geometry_types.items():

            print(
                f"    {geometry_type}: "
                f"{count}"
            )

    # TAG: PROPERTIES
    property_types = result.get(
        "property_types",
        {}
    )

    if property_types:

        print()
        print("PROPERTIES:")

        for key, types in property_types.items():

            type_text = ", ".join(
                sorted(types)
            )

            print(
                f"    {key} -> "
                f"{type_text}"
            )

    print()
    print(
        "[OK] Inspection completed."
    )


# ============================================================
# TAG: COMMAND LINE
# ============================================================

def main():

    # TAG: ALL MODE
    if len(sys.argv) == 2:

        if sys.argv[1] == "--all":

            inspect_all()
            return

    # TAG: SINGLE DATASET
    if len(sys.argv) == 3:

        source = sys.argv[1]
        dataset_id = sys.argv[2]

        inspect_single(
            source,
            dataset_id
        )

        return

    # TAG: HELP
    print()
    print("Usage:")
    print()
    print(
        "  python tools/dataset_inspector.py "
        "SOURCE DATASET_ID"
    )

    print(
        "  python tools/dataset_inspector.py "
        "--all"
    )

    print()


# ============================================================
# TAG: PROGRAM START
# ============================================================

if __name__ == "__main__":

    main()