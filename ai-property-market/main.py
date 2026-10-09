# TAG: ENTRY POINT
# Главната точка за управление на pipeline-а:
#
#   RAW snapshot -> Normalizer -> Deduplicator
#                -> Consolidator (entity match + clusters)
#                -> Finished Exporter (price history)
#                -> Search API / Dashboard
#
# РЕЖИМ НА РАБОТА:
#   Свалянето на данни от външни източници е ИЗКЛЮЧЕНО по
#   подразбиране (вж. netpolicy.py). Проектът трябва да може да
#   се клонира и да се пусне без интернет, затова default-ът е
#   локален pipeline върху данните в sample_data/.
#
# ИЗПОЛЗВАНЕ:
#   python main.py --setup        еднократно копира sample_data/ -> storage_raw/
#   python main.py --pipeline     нормализиране -> дедуп -> консолидация -> експорт
#   python main.py --api          стартира Flask API + Dashboard
#   python main.py --demo         setup + pipeline + api (най-честият случай)
#   python main.py --tests        пълната тестова галерия
#
#   python main.py --collect      блокирано: мрежата е изключена
#   python main.py --fetch-all    блокирано: мрежата е изключена
#   python main.py --full         блокирано: изисква мрежа
#   (за да се разреши: set AI_PROPERTY_ALLOW_NETWORK=1)

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional


# ============================================================
# TAG: PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "storage_raw"
STORAGE_DIR = BASE_DIR / "storage"
SAMPLE_DIR = BASE_DIR / "sample_data"

sys.path.insert(0, str(BASE_DIR))


def _blocked(flag: str) -> None:
    """
    Обяснява защо флагът за сваляне не работи и какво да се
    направи, ако все пак е нужно.
    """

    import netpolicy

    print()
    print("=" * 70)
    print(f" {flag}: БЛОКИРАНО")
    print("=" * 70)
    print()
    print(f"Операцията сваля данни от външен източник.")
    print(f"Мрежата е изключена (netpolicy.py, {netpolicy.ENV_VAR}).")
    print("Проектът се разпространява, без да тегли чужди данни.")
    print()
    print("За да го разрешите за собствена употреба:")
    print(f"    set {netpolicy.ENV_VAR}=1")
    print()
    print("Без мрежа проектът работи изцяло върху sample_data/:")
    print("    python main.py --setup")
    print("    python main.py --pipeline")
    print("    python main.py --api")
    print()


# ============================================================
# TAG: SETUP - локалните примерни данни -> storage_raw/
# ============================================================

def step_setup(force: bool = False) -> int:
    """
    Копира вградения примерен набор в storage_raw/.

    Това е начина, по който човек, клонирал репозитото,
    получава данни за обработка - без нито една мрежова
    заявка. Идемпотентно: ако вече има данни и не е --
    force, нищо не прави.
    """

    print()
    print("=" * 70)
    print(" SETUP: подготовка на локалните примерни данни")
    print("=" * 70)
    print()

    if not SAMPLE_DIR.exists():
        print(f"[FAIL] sample_data/ липсва: {SAMPLE_DIR}")
        return 1

    existing = (
        [p for p in RAW_DIR.iterdir() if p.is_dir()]
        if RAW_DIR.exists() else []
    )

    if existing and not force:
        print(f"[OK] storage_raw/ вече съдържа данни: "
              f"{', '.join(p.name for p in existing)}")
        print("     Нищо не е променено. За ново копирание: --setup --force")
        return 0

    for p in existing:
        shutil.rmtree(p)
        print(f"[DEL] storage_raw/{p.name}/")

    copied = 0
    for source in sorted(SAMPLE_DIR.rglob("*")):
        target = RAW_DIR / source.relative_to(SAMPLE_DIR)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

    print(f"[OK] Копирани {copied} файла в storage_raw/")

    for p in sorted(RAW_DIR.rglob("sofiaplan_datasets.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        n = len(data) if isinstance(data, list) else 0
        print(f"     {p.parent.name}: каталог с {n} набора")

    print()
    print("Следваща стъпка: python main.py --pipeline")
    print()

    return 0


# ============================================================
# TAG: NORMALIZER
# ============================================================

def step_normalize(cancel_event=None) -> int:
    print()
    print("=" * 70)
    print(" STEP 1 / 4: NORMALIZER - унифициране на схемите")
    print("=" * 70)
    print()
    from tools import normalizer
    return normalizer.main(cancel_event=cancel_event)


# ============================================================
# TAG: DEDUPLICATOR
# ============================================================

def step_dedup() -> int:
    print()
    print("=" * 70)
    print(" STEP 2 / 4: DEDUPLICATOR - премахване на точни дубликати")
    print("=" * 70)
    print()
    from tools.deduplicator import run_deduplication
    return run_deduplication()


# ============================================================
# TAG: CONSOLIDATOR
# ============================================================

def step_consolidate(fresh_matcher: bool = False) -> Optional[Path]:
    print()
    print("=" * 70)
    print(" STEP 3 / 4: CONSOLIDATOR - entity match + clusters")
    print("=" * 70)
    print()
    from tools.consolidator import run_consolidation
    return run_consolidation(use_existing_matches=not fresh_matcher)


# ============================================================
# TAG: FINISHED EXPORTER
# ============================================================

def step_export_finished(auto_consolidate: bool = True) -> Optional[Path]:
    print()
    print("=" * 70)
    print(" STEP 4 / 4: FINISHED EXPORTER - финален файл + price history")
    print("=" * 70)
    print()
    from tools.finished_exporter import run_export
    result = run_export(auto_consolidate=auto_consolidate)
    if not result:
        print("[FAIL] No finished export produced.")
    return result


# ============================================================
# TAG: API / DASHBOARD
# ============================================================

def step_run_api(host: str, port: int, debug: bool = False) -> None:
    from tools.search_api import main as api_main
    sys.argv = ["search_api", "--host", host, "--port", str(port)]
    if debug:
        sys.argv.append("--debug")
    try:
        api_main()
    except SystemExit:
        pass


# ============================================================
# TAG: TESTS
# ============================================================

def step_tests() -> int:
    print()
    print("=" * 70)
    print(" ТЕСТОВА ГАЛЕРИЯ")
    print("=" * 70)
    print()
    import pytest
    return pytest.main(["tests", "-q"])


# ============================================================
# TAG: LOCAL PIPELINE
# ============================================================

def run_local_pipeline(fresh_matcher: bool = False) -> int:
    print()
    print("=" * 70)
    print(" ЛОКАЛЕН PIPELINE (без мрежа)")
    print(" normalize -> dedup -> consolidate -> export")
    print("=" * 70)

    rc = step_normalize()
    if rc not in (0, 130):
        print(f"[WARN] Normalizer exited with code {rc}.")

    rc = step_dedup()
    if rc != 0:
        print(f"[WARN] Deduplicator exited with code {rc}.")

    consol = step_consolidate(fresh_matcher=fresh_matcher)

    finished = step_export_finished(auto_consolidate=(consol is None))
    if finished is None:
        print()
        print("[FAIL] Pipeline failed to produce finished output.")
        print("       Провери дали има данни в storage_raw/ (python main.py --setup)")
        print()
        return 2

    print()
    print("=" * 70)
    print(" PIPELINE ЗАВЪРШИ УСПЕШНО")
    print("=" * 70)
    print(f"[OK] Finished directory: {finished}")
    print(f"[OK] Ready file:        {finished / 'finished_properties.json'}")
    print(f"[OK] Metadata:          {finished / 'metadata.json'}")
    print()
    print("СЛЕДВАЩА СТЪПКА:  python main.py --api")
    print()

    return 0


# ============================================================
# TAG: WIPE DATA
# ============================================================

def step_wipe_data() -> int:
    print()
    print("=" * 70)
    print(" WIPE DATA - безопасно изтриване на runtime данни")
    print("=" * 70)
    print()

    deleted = 0

    sub_dirs = [
        "analytics", "consolidated", "deduplicated",
        "discovery", "finished", "indexes", "matched", "normalized",
    ]
    if STORAGE_DIR.exists():
        for sub in sub_dirs:
            p = STORAGE_DIR / sub
            if p.is_dir():
                try:
                    shutil.rmtree(p)
                    print(f"[DEL] storage/{sub}/")
                    deleted += 1
                except Exception as e:
                    print(f"[WARN] storage/{sub}/: {e}")
        settings_file = STORAGE_DIR / "settings.json"
        if settings_file.exists():
            try:
                settings_file.unlink()
                print("[DEL] storage/settings.json")
                deleted += 1
            except Exception as e:
                print(f"[WARN] settings.json: {e}")

    if RAW_DIR.exists():
        for p in RAW_DIR.iterdir():
            if p.is_dir():
                try:
                    shutil.rmtree(p)
                    print(f"[DEL] storage_raw/{p.name}/")
                    deleted += 1
                except Exception as e:
                    print(f"[WARN] storage_raw/{p.name}/: {e}")

    for root in (BASE_DIR, BASE_DIR / "collector", BASE_DIR / "tools"):
        if not root.exists():
            continue
        for pycache in root.rglob("__pycache__"):
            if pycache.is_dir() and ".venv" not in str(pycache):
                try:
                    shutil.rmtree(pycache)
                    print(f"[DEL] {pycache.relative_to(BASE_DIR)}/")
                    deleted += 1
                except Exception as e:
                    print(f"[WARN] {pycache}: {e}")

    print()
    print(f"[OK] Изтрити общо: {deleted} обекта.")
    print("     sample_data/ НЕ е засегнат - той е входът.")
    print()
    print("СЛЕДВАЩА СТЪПКА:  python main.py --setup && python main.py --pipeline")
    print()

    return 0


# ============================================================
# TAG: MAIN
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "AI Property Market - data pipeline and search API. "
            "Downloads are disabled by default; the project runs "
            "on the bundled sample data in sample_data/."
        ),
    )

    parser.add_argument(
        "--setup", action="store_true",
        help="Copy sample_data/ into storage_raw/ (offline, run once)",
    )
    parser.add_argument(
        "--setup-force", action="store_true",
        help="Redo --setup even if storage_raw/ already has data",
    )
    parser.add_argument(
        "--pipeline", action="store_true",
        help="Run the local pipeline: normalize -> dedup -> consolidate -> export",
    )
    parser.add_argument(
        "--consolidate-fresh", action="store_true",
        help="With --pipeline: rebuild the matcher index instead of reusing it",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="setup + pipeline + api, in one go",
    )
    parser.add_argument(
        "--api", action="store_true",
        help="Start the Flask API + dashboard",
    )
    parser.add_argument(
        "--tests", action="store_true",
        help="Run the whole pytest suite",
    )
    parser.add_argument(
        "--wipe-data", action="store_true",
        help="Delete all runtime data (never touches code or sample_data/)",
    )

    parser.add_argument(
        "--host", default="127.0.0.1",
        help="API host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="API port (default: 5000)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Flask debug mode",
    )

    blocked = parser.add_argument_group(
        "disabled without AI_PROPERTY_ALLOW_NETWORK=1"
    )
    blocked.add_argument(
        "--collect", action="store_true",
        help=argparse.SUPPRESS,
    )
    blocked.add_argument(
        "--fetch-all", action="store_true",
        help=argparse.SUPPRESS,
    )
    blocked.add_argument(
        "--fetch-ids", type=str, default=None,
        help=argparse.SUPPRESS,
    )
    blocked.add_argument(
        "--full", action="store_true",
        help=argparse.SUPPRESS,
    )

    return parser


BLOCKING_FLAGS = {
    "--collect": "--collect",
    "--fetch-all": "--fetch-all",
    "--fetch-ids": "--fetch-ids",
    "--full": "--full",
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    requested = [
        flag for flag in BLOCKING_FLAGS
        if getattr(args, flag.lstrip("-").replace("-", "_"), None)
    ]

    if requested:
        for flag in requested:
            _blocked(flag)
        return 2

    if args.wipe_data:
        step_wipe_data()

    if args.setup or args.demo or args.setup_force:
        if step_setup(force=args.setup_force) != 0:
            return 1

    if args.pipeline or args.demo:
        rc = run_local_pipeline(
            fresh_matcher=args.consolidate_fresh
        )
        if rc != 0 and not args.api:
            return rc

    if args.tests:
        rc = step_tests()
        if rc != 0 and not args.api:
            return rc

    if args.api:
        step_run_api(host=args.host, port=args.port, debug=args.debug)
        return 0

    if not (args.wipe_data or args.setup or args.setup_force
            or args.pipeline or args.demo or args.tests):
        parser.print_help()
        print()
        print("Най-честият случай:  python main.py --demo")
        print("Само тестовете:      python main.py --tests")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)