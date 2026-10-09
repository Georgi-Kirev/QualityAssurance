# TAG: DATASET FETCHER
# Универсален инструмент за изтегляне и записване на RAW dataset.
# Source-specific API логиката постепенно ще бъде изнесена към collectors.
#
# V2: Паралелизъм чрез ProcessPoolExecutor + rate limiting + retry.

import json
import os
import re
import sys
import threading
import time

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from netpolicy import require_network

from tools.parallel_runner import run_paced
from tools.resources import (
    calculate_resources,
    lower_process_priority,
)


TIMEZONE = ZoneInfo("Europe/Sofia")


# TAG: PROJECT ROOT
BASE_DIR = Path(__file__).resolve().parent.parent


# TAG: RAW STORAGE
RAW_DIR = BASE_DIR / "storage_raw"


# TAG: SOURCE
DEFAULT_SOURCE = "sofiaplan"


# TAG: SOFIAPLAN API
SOFIAPLAN_API = "https://api.sofiaplan.bg/datasets"


# TAG: FETCHER CONFIGURATION
# Колко заявки в секунда да позволяваме глобално.
# SofiaPlan има ~1 заявка/секунда лимит.
REQUESTS_PER_SECOND = 3

# Максимум опити при грешка (HTTP 500, timeout, връзка).
MAX_ATTEMPTS = 3

# Изчакване между опитите (в секунди).
RETRY_BACKOFF_SECONDS = 2.0

# Timeout за една заявка (в секунди).
REQUEST_TIMEOUT = 120

# Размер на блока при стриймване на файла към диск.
# Файловете са до ~630 MB и НИКОГА не се зареждат
# изцяло в паметта.
STREAM_CHUNK_BYTES = 1024 * 1024


# TAG: SIZE GUARD
#
# Няколко слоя на SofiaPlan са по 300-600 MB (dataset 505 е
# 600 MB, 540 е 517 MB, 503 е 471 MB). Един такъв файл:
#   * изчерпва RAM на машината при паралелно сваляне;
#   * дава НУЛЕВА полза - тези слоеве са кадастърни масиви и
#     нямат нито цена, нито адрес.
#
# Затова файл, който по Content-Length надхвърля лимита, се
# прескача ПРЕДИ да се свали тялото. По-малките слоеве се
# държат, за да остане разнообразие.
#
# Лимитът се чете от settings.json -> fetch.max_dataset_mb
# (по подразбиране 150 MB; 0 = без лимит).

DEFAULT_MAX_DATASET_MB = 150


class DatasetTooLarge(Exception):
    """
    Dataset-ът надхвърля разрешения лимит и умишлено не се сваля.

    Отделен тип, за да НЕ се брои за грешка и да не се
    повтаря още MAX_ATTEMPTA пъти - повторният опит би
    свалил същия голям файл отново.
    """

    def __init__(self, size_bytes, limit_bytes):
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"dataset too large: "
            f"{size_bytes / 1048576:.1f} MB > "
            f"limit {limit_bytes / 1048576:.1f} MB"
        )


def get_max_dataset_bytes() -> int:
    """
    Лимит в байтове. 0 = без лимит.

    Четем от settings.json, но падаме на подразбиране, ако
    модулът е използван извън пълния pipeline.
    """
    try:
        from tools.settings import get_max_dataset_mb
        limit_mb = get_max_dataset_mb()
    except Exception:
        limit_mb = DEFAULT_MAX_DATASET_MB

    if limit_mb <= 0:
        return 0

    return int(limit_mb * 1024 * 1024)


# ============================================================
# TAG: RATE LIMITING
# ============================================================
#
# ВАЖНО: при multiprocessing "spawn" (използва се на Windows)
# всеки worker процес получава СВОЕ копие на module-level
# състояние. Затова limiter, който живее в worker-а, НЕ е
# глобален — 12 worker-а x 3 req/s = 36 req/s вместо 3,
# което нарушава лимита на SofiaPlan (~1 req/s).
#
# Решение: ограничението се налага ЦЕНТРАЛНО, в родителския
# процес, преди всяко submit (_RatePacer -> before_submit).
# Така всички worker-и ЗАЕМАТ заедно един и същ лимит.
#
# _wait_for_rate_limit() остава само за директни,
# единични извиквания извън паралелния режим.

_rate_lock = threading.Lock()
_last_request_time = [0.0]


def _wait_for_rate_limit():
    """
    Ограничава скоростта В САМОТЕН ПРОЦЕС.

    Използва се само при директно извикване на
    download_dataset() без ProcessPool.
    """

    if REQUESTS_PER_SECOND <= 0:
        return

    min_interval = 1.0 / REQUESTS_PER_SECOND

    with _rate_lock:

        now = time.monotonic()

        elapsed = now - _last_request_time[0]

        if elapsed < min_interval:

            sleep_time = min_interval - elapsed

            time.sleep(sleep_time)

        _last_request_time[0] = time.monotonic()


class _RatePacer:
    """
    Централен rate limiter — работи в РОДИТЕЛСКИЯ процес.

    Извиква се веднъж на всяко submit и спира, ако трябва.
    Гарантира глобално REQUESTS_PER_SECOND, независимо от
    броя на worker-и.
    """

    def __init__(self, requests_per_second):
        self._min_interval = (
            1.0 / requests_per_second
            if requests_per_second > 0
            else 0.0
        )
        self._next_allowed = 0.0

    def wait(self, _payload=None):
        """
        Изчаква до следващия позволен момент за заявка.

        _payload е приет, защото run_paced() извиква
        before_submit(payload) - т.е. очаква функция с един
        аргумент. Ограничаването на скоростта не зависи от
        това WHICH dataset се сваля, затова аргументът се
        игнорира.

        Без този параметър паралелното сваляне падаше с
        "wait() takes 1 positional argument but 2 were given"
        още при първия submit.
        """
        if self._min_interval <= 0:
            return

        with _rate_lock:

            now = time.monotonic()

            delay = self._next_allowed - now

            if delay > 0:

                time.sleep(delay)

                now = time.monotonic()

            self._next_allowed = (
                now + self._min_interval
            )



# ============================================================
# TAG: FORMAT DETECTION
# ============================================================

# Колко байта се четат за определяне на формата.
# Преди това се четеше ЦЕЛИЯТ файл и json.loads() го
# парсваше - при файл от 628 MB това изяде ~5 GB RAM
# (измерено усилване 8.3x). Един файл беше достатъчен
# да изчерпа паметта на машината.
FORMAT_SNIFF_BYTES = 64 * 1024

# Повече от това - значи не е JSON/GeoJSON, а често
# голям двоичен файл (DTM, raster). Не се опитваме да го
# парсваме.
MAX_SNIFF_PARSE_BYTES = 16 * 1024 * 1024


def detect_format(data: bytes, content_type: str) -> str:

    content_type = content_type.lower()

    # TAG: CONTENT TYPE
    if "zip" in content_type:
        return "zip"

    if "7z" in content_type:
        return "bin"

    if "csv" in content_type:
        return "csv"

    # TAG: JSON DETECTION
    # Само ОГРАНИЧЕН префикс - никога целия файл.
    sample = data[:FORMAT_SNIFF_BYTES]

    if b"\x00" in sample:
        return "bin"

    try:

        text = sample.decode("utf-8").lstrip()

    except UnicodeDecodeError:
        return "bin"

    stripped = text.lstrip()

    if not stripped.startswith(("{", "[")):
        return "bin"

    # --------------------------------------------------------
    # TAG: GEOJSON / JSON - РЕГЕКС, НЕ ПАРС
    #
    # БЪГ: тук някога се опитваше json.loads() върху ПЪЛНИЯ
    # прочитан префикс. Но data е вече отрязан до
    # FORMAT_SNIFF_BYTES (64 KB), а всички JSON файлове над
    # 64 KB се прерязват => JSONDecodeError => падаше надолу
    # до "txt"/"bin".
    #
    # Последица: dataset 624 (ikonomika.imoti_ceni_ge) -
    # ЕДИНСТВЕНИЯТ слой с цени, 9.9 MB - се записваше като
    # raw_data.txt вместо raw_data.geojson. Оттам идва и
    # следващият бъг в нормализатора.
    #
    # Решение: разпознаваме по структура, не чрез парсване.
    # GeoJSON винаги започва с FeatureCollection/Feature, което
    # се вижда в първите няколкостотин байта.
    # --------------------------------------------------------

    head = stripped[:4096]

    if (
        re.search(
            r'"type"\s*:\s*"FeatureCollection"',
            head,
            re.IGNORECASE,
        )
        or re.search(
            r'"type"\s*:\s*"Feature"',
            head,
            re.IGNORECASE,
        )
        or '"features"' in head
    ):

        return "geojson"

    # Малък файл, който наистина може да се затвори целия ->
    # пълен парс дава по-силен сигнал (напр. връща "json" за
    # обикновен JSON обект). За отрязан документ парсът просто
    # пада и отиваме към структурния отговор по-горе.
    try:

        parsed = json.loads(text)

        if isinstance(parsed, dict):

            if (
                parsed.get("type") == "FeatureCollection"
                or "features" in parsed
            ):
                return "geojson"

            return "json"

        if isinstance(parsed, list):
            return "json"

    except Exception:
        pass

    return "json"


# ============================================================
# TAG: CURRENT TIMESTAMP
# ============================================================

def current_timestamp() -> str:
    """
    Bulgaria local timestamp.

    Format:
        DD-MM-YYYY_HH
    """

    now = datetime.now(TIMEZONE)

    return now.strftime("%d-%m-%Y_%H")


# ============================================================
# TAG: DOWNLOAD ONE DATASET (single attempt)
# ============================================================

def _download_once(
    dataset_id: int,
    source: str,
    date_folder: str,
    rate_limited_by_parent: bool = False,
):
    """
    Един опит за сваляне на dataset.

    rate_limited_by_parent=True -> не се извиква локалният
    limiter, защото лимитът вече е наложен централно от
    _RatePacer в родителския процес.

    Връща:
        (True,  info)   при успех
        (False, error)  при грешка
    """

    # TAG: SOURCE URL
    if source == "sofiaplan":

        api_url = (
            f"{SOFIAPLAN_API}/{dataset_id}"
        )

    else:

        return (
            False,
            f"Unknown source: {source}",
        )

    # TAG: OUTPUT DIRECTORY
    dataset_dir = (
        RAW_DIR /
        source /
        date_folder /
        "datasets" /
        str(dataset_id)
    )

    dataset_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # TAG: RATE LIMIT
    if not rate_limited_by_parent:
        _wait_for_rate_limit()

    # TAG: REQUEST
    request = Request(
        api_url,
        headers={
            "User-Agent": "PropertyDataCollector/1.0"
        },
    )

    # TAG: NETWORK GATE
    require_network(f"dataset {dataset_id} from SofiaPlan")

    # TAG: DOWNLOAD (STREAMING)
    #
    # Тук се пише направо на диск, а НЕ се чете в RAM.
    # Преди това беше `data = response.read()`, което за
    # файл от 628 MB (dataset 505) заемаше ~5 GB памет
    # само за да разпознае формата. С 12 паралелни
    # worker-а това изчерпваше машината.
    #
    temp_file = (
        dataset_dir / "raw_data.download"
    )

    try:

        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:

            content_type = response.headers.get(
                "Content-Type",
                "",
            )

            # TAG: EARLY SIZE CHECK
            #
            # Content-Length се чете ПРЕДИ свалянето на тялото.
            # Ако липсва, падаме на проверката по време на
            # stream-а долу.
            max_bytes = get_max_dataset_bytes()

            declared = response.headers.get(
                "Content-Length"
            )

            if max_bytes and declared:

                try:
                    declared_bytes = int(declared)
                except (TypeError, ValueError):
                    declared_bytes = 0

                if declared_bytes > max_bytes:

                    raise DatasetTooLarge(
                        declared_bytes,
                        max_bytes,
                    )

            with temp_file.open("wb") as sink:

                written = 0

                while True:

                    chunk = response.read(
                        STREAM_CHUNK_BYTES
                    )

                    if not chunk:
                        break

                    written += len(chunk)

                    # TAG: STREAMING SIZE CHECK
                    # Safety net, ако сървърът не е дал
                    # Content-Length. Спираме веднага, вместо
                    # да пълним диска докрай.
                    if max_bytes and written > max_bytes:

                        raise DatasetTooLarge(
                            written,
                            max_bytes,
                        )

                    sink.write(chunk)

        downloaded_bytes = temp_file.stat().st_size

        if downloaded_bytes == 0:

            raise ValueError(
                "Empty response body"
            )

    except DatasetTooLarge as error:

        temp_file.unlink(missing_ok=True)

        # Умишлено прескачане, не грешка. Не се ретраи.
        return (
            False,
            f"SKIPPED_TOO_LARGE: {error}",
        )

    except HTTPError as error:

        temp_file.unlink(missing_ok=True)

        return (
            False,
            f"HTTP Error {error.code}: {error.reason}",
        )

    except URLError as error:

        temp_file.unlink(missing_ok=True)

        return (
            False,
            f"URL Error: {error.reason}",
        )

    except Exception as error:

        temp_file.unlink(missing_ok=True)

        return (
            False,
            f"{type(error).__name__}: {error}",
        )

    # TAG: DETECT FORMAT (от ОГРАНИЧЕН префикс)
    with temp_file.open("rb") as probe:
        sniff_prefix = probe.read(
            FORMAT_SNIFF_BYTES
        )

    detected_format = detect_format(
        sniff_prefix,
        content_type,
    )

    # TAG: FILE EXTENSION
    extension = f".{detected_format}"

    # TAG: SAVE RAW (атомарно преименуване)
    raw_file = (
        dataset_dir /
        f"raw_data{extension}"
    )

    os.replace(
        temp_file,
        raw_file
    )

    # TAG: METADATA
    now = datetime.now(TIMEZONE)

    metadata = {

        "dataset_id": dataset_id,

        "source": source,

        "downloaded_at": now.isoformat(),

        "api_url": api_url,

        "content_type": content_type,

        "detected_format": detected_format,

        "file_size_bytes": downloaded_bytes,

        "raw_file": raw_file.name,
    }

    # TAG: METADATA FILE
    metadata_file = (
        dataset_dir /
        "metadata.json"
    )

    metadata_file.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # TAG: RESULT
    return (
        True,
        {
            "dataset_id": dataset_id,
            "source": source,
            "raw_file": str(raw_file),
            "format": detected_format,
            "content_type": content_type,
            "size_bytes": downloaded_bytes,
        },
    )


# ============================================================
# TAG: DOWNLOAD DATASET (with retry)
# ============================================================

def download_dataset(
    dataset_id: int,
    source: str = DEFAULT_SOURCE,
    date_folder: str = None,
    rate_limited_by_parent: bool = False,
):
    """
    Сваля един dataset с retry логика.

    Параметри:
        dataset_id   — ID на dataset
        source       — "sofiaplan"
        date_folder  — timestamp папка. Ако е None, изчислява се сега.
                       (Подава се от fetch_all, за да е общ за целия run.)
        rate_limited_by_parent — True, когато лимитът вече е наложен
                       централно от _RatePacer (паралелен режим).

    Връща:
        Path към raw файла при успех, None при окончателна грешка.
    """

    if date_folder is None:
        date_folder = current_timestamp()

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):

        ok, info = _download_once(
            dataset_id,
            source,
            date_folder,
            rate_limited_by_parent=rate_limited_by_parent,
        )

        if ok:

            return Path(info["raw_file"])

        last_error = info

        # Ако е грешка, която няма смисъл да се retry-ва — спираме.
        if (
            "Unsupported" in str(info)
            or "Unknown source" in str(info)
            or "SKIPPED_TOO_LARGE" in str(info)
        ):
            break

        if attempt < MAX_ATTEMPTS:

            time.sleep(
                RETRY_BACKOFF_SECONDS * attempt
            )

    # Всички опити неуспешни.
    return None


# ============================================================
# TAG: WORKER (за ProcessPoolExecutor)
# ============================================================

def _download_worker(args):
    """
    Worker функция за паралелно сваляне.

    ВАЖНО: ProcessPoolExecutor не може да подава сложни
    обекти лесно, затова подаваме tuple от прости стойности.

    Връща dict с резултат.
    """

    dataset_id, source, date_folder = args

    try:

        raw_file = download_dataset(
            dataset_id=dataset_id,
            source=source,
            date_folder=date_folder,
            rate_limited_by_parent=True,
        )

        if raw_file is None:

            return {
                "status": "failed",
                "dataset_id": dataset_id,
                "error": "Download failed after all retries.",
            }

        return {
            "status": "success",
            "dataset_id": dataset_id,
            "raw_file": str(raw_file),
        }

    except Exception as error:

        return {
            "status": "failed",
            "dataset_id": dataset_id,
            "error": (
                f"{type(error).__name__}: {error}"
            ),
        }


# ============================================================
# TAG: FETCH ALL (parallel)
# ============================================================

# ============================================================
# TAG: CANCEL CLEANUP
# ============================================================

def cleanup_partial_downloads(
    args_list,
    source: str = DEFAULT_SOURCE,
    date_folder: str = None,
) -> int:
    """
    Изчиства останалите temp файлове от прекъснат fetch.

    При отказ по средата някои worker-и може да са оставили
    raw_data.download (до 630 MB всеки). Без почистване те
    заемат диск и остават завинаги.

    Изтрива САМО .download файловете и празните директории.
    Успешно свалените raw_data.* файлове НЕ се пипат.
    """

    if date_folder is None:

        return 0

    removed = 0

    for args in args_list or []:

        dataset_id = args[0]

        try:

            dataset_dir = (
                RAW_DIR /
                source /
                date_folder /
                "datasets" /
                str(dataset_id)
            )

            temp_file = dataset_dir / "raw_data.download"

            if temp_file.exists():

                temp_file.unlink()

                removed += 1

            # Празна директория = само partial артефакт.
            if (
                dataset_dir.exists()
                and not any(dataset_dir.iterdir())
            ):

                dataset_dir.rmdir()

        except Exception:

            pass

    return removed


# ============================================================
# TAG: FETCH ALL (parallel)
# ============================================================

def fetch_all(
    dataset_ids,
    source: str = DEFAULT_SOURCE,
    cancel_event=None,
):
    """
    Сваля много dataset-и паралелно.

    Параметри:
        dataset_ids  — iterable от int ID-та
        source       — "sofiaplan"
        cancel_event — threading.Event за отказ по средата

    Връща:
        dict с обобщение:
            {
                "snapshot": date_folder,
                "total": N,
                "success": X,
                "failed": Y,
                "cancelled": bool,
                "successful_ids": [...],
                "failed_ids": [...],
                "errors": {id: "error text", ...},
            }
    """

    dataset_ids = list(dataset_ids)

    if not dataset_ids:

        return {
            "snapshot": None,
            "total": 0,
            "success": 0,
            "failed": 0,
            "cancelled": False,
            "successful_ids": [],
            "failed_ids": [],
            "errors": {},
        }

    # --------------------------------------------------------
    # TAG: ONE SNAPSHOT FOR THE WHOLE RUN
    # --------------------------------------------------------

    date_folder = current_timestamp()

    # --------------------------------------------------------
    # TAG: RESOURCES
    # --------------------------------------------------------

    resources = calculate_resources()

    max_workers = resources["ceiling"]

    print()
    print("================================")
    print(" DATASET FETCHER (parallel)")
    print("================================")
    print()

    print(
        f"[RESOURCE] Physical CPU cores: "
        f"{resources['cpu_count']}"
    )

    print(
        f"[RESOURCE] Reserved cores: "
        f"{resources['reserved_cores']}"
    )

    print(
        f"[RESOURCE] Workers ceiling: "
        f"{max_workers}"
    )

    print(
        f"[RESOURCE] Workers at start: "
        f"{resources['workers']}"
    )

    print(
        f"[RESOURCE] Workers are scaled "
        f"DYNAMICALLY during the run."
    )

    print(
        f"[RESOURCE] Rate limit: "
        f"{REQUESTS_PER_SECOND} req/s "
        f"(GLOBAL, enforced in parent process)"
    )

    print(
        f"[INFO] Snapshot folder: "
        f"{date_folder}"
    )

    print(
        f"[INFO] Datasets to download: "
        f"{len(dataset_ids)}"
    )

    print()

    # --------------------------------------------------------
    # TAG: PARALLEL EXECUTION
    # --------------------------------------------------------

    successful_ids = []
    failed_ids = []
    errors = {}

    done = 0
    total = len(dataset_ids)

    args_list = [
        (dataset_id, source, date_folder)
        for dataset_id in dataset_ids
    ]

    # Централен rate limiter — споделен за всички worker-и.
    pacer = _RatePacer(REQUESTS_PER_SECOND)

    def handle_result(
        args,
        result,
        error,
    ):

        nonlocal done

        dataset_id = args[0]

        if error is not None:

            result = {
                "status": "failed",
                "dataset_id": dataset_id,
                "error": (
                    f"{type(error).__name__}: {error}"
                ),
            }

        done += 1

        if result["status"] == "success":

            successful_ids.append(
                result["dataset_id"]
            )

            print(
                f"[{done}/{total}] "
                f"[OK] dataset "
                f"{result['dataset_id']}"
            )

        else:

            failed_ids.append(
                result["dataset_id"]
            )

            errors[
                result["dataset_id"]
            ] = result.get(
                "error",
                "unknown error",
            )

            print(
                f"[{done}/{total}] "
                f"[FAILED] dataset "
                f"{result['dataset_id']} "
                f"-> {result.get('error', '')}"
            )

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=lower_process_priority,
    ) as executor:

        run = run_paced(
            args_list,
            _download_worker,
            executor,
            on_result=handle_result,
            cancel_event=cancel_event,
            target_workers=max_workers,
            before_submit=pacer.wait,
            on_critical=lambda plan: print(
                f"[RESOURCE] BLOCKED: "
                f"{'; '.join(plan['reasons'])}"
            ),
        )

    # --------------------------------------------------------
    # TAG: CANCEL CLEANUP
    # --------------------------------------------------------

    if run["cancelled"]:

        removed = cleanup_partial_downloads(
            args_list,
            source,
            date_folder,
        )

        print()

        print(
            f"[CANCEL] Stopped by user. "
            f"Removed {removed} partial download(s)."
        )

    # --------------------------------------------------------
    # TAG: SUMMARY
    # --------------------------------------------------------

    print()
    print("================================")
    print(
        " FETCHER CANCELLED"
        if run["cancelled"]
        else " FETCHER FINISHED"
    )
    print("================================")
    print()

    print(
        f"[RESULT] Total: {total}"
    )

    print(
        f"[RESULT] Processed: {done}"
    )

    print(
        f"[RESULT] Successful: "
        f"{len(successful_ids)}"
    )

    print(
        f"[RESULT] Failed: "
        f"{len(failed_ids)}"
    )

    if run["cancelled"]:

        print(
            f"[RESULT] Not processed: "
            f"{total - done}"
        )

    if failed_ids:

        print()
        print("[WARNING] Failed dataset IDs:")

        for dataset_id in failed_ids:

            print(
                f"  - {dataset_id}: "
                f"{errors.get(dataset_id, '')}"
            )

    return {
        "snapshot": date_folder,
        "total": total,
        "success": len(successful_ids),
        "failed": len(failed_ids),
        "cancelled": run["cancelled"],
        "not_processed": total - done,
        "successful_ids": successful_ids,
        "failed_ids": failed_ids,
        "errors": errors,
    }


# ============================================================
# TAG: COMMAND LINE
# ============================================================

def main():

    # TAG: ARGUMENT CHECK
    if len(sys.argv) < 2:

        print(
            "Usage:"
        )

        print(
            "python -m tools.dataset_fetcher DATASET_ID [SOURCE]"
        )

        print(
            "python -m tools.dataset_fetcher --all ID1,ID2,ID3"
        )

        print()

        print(
            "Example:"
        )

        print(
            "python -m tools.dataset_fetcher 626"
        )

        return

    # TAG: BATCH MODE
    if sys.argv[1] == "--all":

        if len(sys.argv) < 3:

            print(
                "[ERROR] --all requires a CSV list of IDs."
            )

            return

        try:

            dataset_ids = [
                int(x.strip())
                for x in sys.argv[2].split(",")
                if x.strip()
            ]

        except ValueError:

            print(
                "[ERROR] All IDs must be integers."
            )

            return

        source = (
            sys.argv[3]
            if len(sys.argv) >= 4
            else DEFAULT_SOURCE
        )

        fetch_all(dataset_ids, source)

        return

    # TAG: SINGLE MODE
    try:

        dataset_id = int(sys.argv[1])

    except ValueError:

        print(
            "[ERROR] Dataset ID must be a number."
        )

        return

    source = (
        sys.argv[2]
        if len(sys.argv) >= 3
        else DEFAULT_SOURCE
    )

    # TAG: DOWNLOAD SINGLE
    raw_file = download_dataset(
        dataset_id,
        source,
    )

    if raw_file is None:

        print(
            f"[FAILED] Could not download dataset "
            f"{dataset_id}."
        )

    else:

        print(
            f"[OK] Saved to: {raw_file}"
        )


# TAG: PROGRAM START
if __name__ == "__main__":
    main()