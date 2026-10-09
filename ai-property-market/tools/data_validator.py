# TAG: DATA VALIDATOR
# Универсален инструмент за проверка на големи JSON файлове.
#
# Основна цел:
# - да проверява дали JSON файлът е валиден;
# - да брои записите без зареждане на целия файл в RAM;
# - да проверява структурата;
# - да работи с много големи normalized файлове;
# - да не променя входния файл.

import json
import sys
import time
from pathlib import Path

import ijson


# TAG: PROJECT ROOT
BASE_DIR = Path(__file__).resolve().parent.parent


# TAG: EXPECTED STRUCTURE
EXPECTED_ROOT_TYPE = "array"


# TAG: FORMAT BYTES
def format_bytes(size_bytes: int) -> str:
    """
    Преобразува bytes в удобен за четене формат.
    """

    size = float(size_bytes)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB"
    ]

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PB"


# TAG: RECORD VALIDATION
def validate_record(record, index: int):
    """
    Проверява един normalized record.

    На този етап изискваме само record-ът
    да бъде JSON object.

    Не налагаме конкретна схема,
    защото Validator-ът трябва да остане универсален.
    """

    if not isinstance(record, dict):
        raise ValueError(
            f"Record {index} is not a JSON object. "
            f"Detected type: {type(record).__name__}"
        )


# TAG: STREAM VALIDATION
def validate_json_file(file_path: Path):
    """
    Проверява JSON файла чрез streaming.

    Не зарежда целия файл в памет.
    """

    record_count = 0
    first_record = None
    last_record = None

    # TAG: START TIMER
    start_time = time.perf_counter()

    try:
        with file_path.open(
            "rb"
        ) as file:

            # TAG: STREAM JSON
            # Очакваме root-level JSON array.
            records = ijson.items(
                file,
                "item",
                use_float=True
            )

            for record in records:

                record_count += 1

                # TAG: FIRST RECORD
                if record_count == 1:
                    first_record = record

                # TAG: LAST RECORD
                last_record = record

                # TAG: RECORD VALIDATION
                validate_record(
                    record,
                    record_count
                )

    except Exception as error:

        elapsed = time.perf_counter() - start_time

        return {
            "valid": False,
            "record_count": record_count,
            "first_record": first_record,
            "last_record": last_record,
            "elapsed": elapsed,
            "error": error
        }

    elapsed = time.perf_counter() - start_time

    return {
        "valid": True,
        "record_count": record_count,
        "first_record": first_record,
        "last_record": last_record,
        "elapsed": elapsed,
        "error": None
    }


# TAG: EXPECTED COUNT
def parse_expected_count(value):
    """
    Прочитава очаквания брой записи от command line.
    """

    if value is None:
        return None

    try:
        expected = int(value)

    except ValueError:
        raise ValueError(
            "Expected record count must be an integer."
        )

    if expected < 0:
        raise ValueError(
            "Expected record count cannot be negative."
        )

    return expected


# TAG: RESULT OUTPUT
def print_result(
    file_path: Path,
    result: dict,
    expected_count=None
):
    """
    Извежда резултата от Validator-а.
    """

    print()
    print("================================")
    print(" DATA VALIDATOR")
    print("================================")
    print()

    print(f"[INFO] File:")
    print(file_path)

    print()

    # TAG: FILE SIZE
    file_size = file_path.stat().st_size

    print(
        f"[INFO] Size: "
        f"{format_bytes(file_size)} "
        f"({file_size:,} bytes)"
    )

    print()

    # TAG: VALIDITY
    if result["valid"]:
        print("[OK] JSON structure: VALID")
    else:
        print("[FAILED] JSON structure: INVALID")

    print(
        f"[INFO] Records detected: "
        f"{result['record_count']:,}"
    )

    # TAG: EXPECTED COUNT RESULT
    if expected_count is not None:

        print(
            f"[INFO] Expected records: "
            f"{expected_count:,}"
        )

        if (
            result["valid"]
            and result["record_count"] == expected_count
        ):
            print(
                "[OK] Record count: MATCH"
            )

        else:
            print(
                "[FAILED] Record count: MISMATCH"
            )

    # TAG: FIRST RECORD
    if result["first_record"] is not None:

        print(
            "[OK] First record: VALID JSON object"
        )

    else:

        print(
            "[WARNING] First record: NOT FOUND"
        )

    # TAG: LAST RECORD
    if result["last_record"] is not None:

        print(
            "[OK] Last record: VALID JSON object"
        )

    else:

        print(
            "[WARNING] Last record: NOT FOUND"
        )

    # TAG: TIME
    print()

    print(
        f"[INFO] Validation time: "
        f"{result['elapsed']:.2f}s"
    )

    # TAG: FINAL STATUS
    count_ok = (
        expected_count is None
        or result["record_count"] == expected_count
    )

    final_ok = (
        result["valid"]
        and count_ok
        and result["record_count"] > 0
    )

    print()

    print("================================")

    if final_ok:

        print("[RESULT] VALID")

    else:

        print("[RESULT] INVALID")

        if result["error"] is not None:

            print()
            print(
                f"[ERROR] "
                f"{type(result['error']).__name__}: "
                f"{result['error']}"
            )

    print("================================")
    print()


# TAG: USAGE
def print_usage():
    """
    Показва начина на използване.
    """

    print()
    print(
        "Usage:"
    )

    print(
        '  python tools\\data_validator.py '
        '"path\\to\\file.json"'
    )

    print()

    print(
        "Optional expected record count:"
    )

    print(
        '  python tools\\data_validator.py '
        '"path\\to\\file.json" 475010'
    )

    print()


# TAG: MAIN
def main():

    # TAG: ARGUMENT CHECK
    if len(sys.argv) < 2:

        print_usage()
        sys.exit(1)

    file_path = Path(
        sys.argv[1]
    )

    # TAG: FILE EXISTENCE
    if not file_path.exists():

        print()
        print(
            "[ERROR] File does not exist:"
        )
        print(file_path)
        print()

        sys.exit(1)

    if not file_path.is_file():

        print()
        print(
            "[ERROR] Path is not a file:"
        )
        print(file_path)
        print()

        sys.exit(1)

    # TAG: EXPECTED COUNT
    expected_count = None

    if len(sys.argv) >= 3:

        try:

            expected_count = parse_expected_count(
                sys.argv[2]
            )

        except ValueError as error:

            print()
            print(
                f"[ERROR] {error}"
            )
            print()

            sys.exit(1)

    # TAG: VALIDATE
    result = validate_json_file(
        file_path
    )

    # TAG: PRINT RESULT
    print_result(
        file_path,
        result,
        expected_count
    )

    # TAG: EXIT STATUS
    count_ok = (
        expected_count is None
        or result["record_count"] == expected_count
    )

    if (
        result["valid"]
        and count_ok
        and result["record_count"] > 0
    ):
        sys.exit(0)

    sys.exit(1)


# TAG: PROGRAM START
if __name__ == "__main__":
    main()