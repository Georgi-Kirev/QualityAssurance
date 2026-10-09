# TAG: SETTINGS
# Централен модул за настройките на проекта. Запазва се в
# storage/settings.json. Всички промени СЕ ПРЕЗАПИСВАТ (не append).
#
# ПРОЕКТЪТ Е НЕКОМЕРЦИАЛЕН ХОБИ ПРОЕКТ. Няма плащания,
# няма кредити, няма акаунти на потребители и няма какво
# да се продава. Това е решение, не липса на функционалност:
# първоначалната версия на проекта съдържаше PayPal checkout,
# кредитна система и DEMO/PROD превключвател, и всичко това е
# премахнато - виж README.md, раздел "Какво НЕ е тук".
#
# ОСТАНАЛОТО ТУК е само оперативно: максимален размер на
# dataset (ако се разреши мрежата), настройки на планировчика
# и ниво на логване.

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Europe/Sofia")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORAGE_DIR = PROJECT_ROOT / "storage"
SETTINGS_FILE = STORAGE_DIR / "settings.json"


DEFAULT_SETTINGS: Dict[str, Any] = {
    "schema_version": "1.0",
    "updated_at": None,

    # ПЛАНИРОВЧИК (автоматично пускане на pipeline-а през X часа)
    "schedule": {
        "enabled": False,
        "every_hours": 12,
        "collect_catalog": True,
        "fetch_all_datasets": False,   # твърде голямо, ОСТАВЕТЕ False освен ако знаете какво правите
        "fetch_dataset_ids": [],       # пример: [2,6,31,626]
        "last_run_at": None,
        "last_run_status": None,
        "next_run_at": None,
    },

    # ЛОГВАНЕ
    "query_log_limit": 1000,
    "debug_logs": False,

    # СВАЛЯНЕ НА ДАННИ
    "fetch": {
        # Dataset-и, по-големи от този лимит (в MB), се ПРЕСКАЧАТ.
        # Няколко слоя от SofiaPlan са по 300-600 MB; един
        # заминаваше за 5 GB RAM само за да се разпознае
        # форматът му, а ползата от тях е нулева. По-малките
        # слоеве се държат, за да останат данните разнообразни.
        "max_dataset_mb": 150,

        # Ако е True, прескачаните dataset-и се записват в
        # metadata.json на снимката, за да не ги броиш за
        # липсващи при следващ рън.
        "report_skipped": True,
    },
}


def _ensure_dirs() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _atomic_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _now_ts() -> str:
    return datetime.now(TIMEZONE).strftime("%d-%m-%Y_%H")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> Dict[str, Any]:
    saved = _atomic_read_json(SETTINGS_FILE, None)
    if saved is None:
        data = dict(DEFAULT_SETTINGS)
        data["updated_at"] = _now_ts()
        save_settings(data)
        return data
    merged = _deep_merge(DEFAULT_SETTINGS, saved)
    merged.setdefault("updated_at", _now_ts())
    return merged


def save_settings(new_settings: Dict[str, Any]) -> Dict[str, Any]:
    data = _deep_merge(DEFAULT_SETTINGS, new_settings)
    data["updated_at"] = _now_ts()
    _atomic_write_json(SETTINGS_FILE, data)
    return data


def get_schedule() -> Dict[str, Any]:
    return get_settings().get("schedule", {})


def get_max_dataset_mb() -> float:
    """
    Лимит в MB за един dataset. 0 или по-малко = без лимит.
    """
    raw = get_settings().get("fetch", {}).get("max_dataset_mb", 150)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 150.0
    return max(0.0, value)


def set_schedule_field(field: str, value: Any) -> Dict[str, Any]:
    s = get_settings()
    s["schedule"][field] = value
    return save_settings(s)


def for_frontend() -> Dict[str, Any]:
    """
    Настройките, безопасни за връщане към браузъра.

    В миналото тук се прикриваше PayPal client_secret. След
    премахването на плащанията няма чувствителни полета, но
    функцията остава като единствен вход за dashboard-а -
    така че бъдещо добавяне на такова поле не забравя да се
    прецени тук.
    """
    return get_settings()