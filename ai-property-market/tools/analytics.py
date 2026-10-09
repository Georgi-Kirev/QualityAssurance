# TAG: ANALYTICS
# Анонимна статистика за търсения и популярност на имотите.
# V2.0: Добавени месечни групи - приходи actual vs potential.
#
# ВАЖНО: Не се съхраняват лични данни.
# Записва се само броят на търсенията и "прегледите" за всеки property_id.
# Файлът СЕ ПРЕЗАПИСВА (не се append-ва) за оптимизация.

import json
import os
import sys
from calendar import monthrange
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


TIMEZONE = ZoneInfo("Europe/Sofia")
ANALYTICS_DIR = PROJECT_ROOT / "storage" / "analytics"
STATS_FILE = ANALYTICS_DIR / "search_stats.json"


SEARCH_STATS_SCHEMA_VERSION = "2.0"


def create_timestamp() -> str:
    return datetime.now(TIMEZONE).strftime("%d-%m-%Y_%H")


def _current_month_key() -> str:
    now = datetime.now(TIMEZONE)
    return now.strftime("%Y-%m")


def _ensure_dirs() -> None:
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)


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


def _default_stats() -> Dict[str, Any]:
    return {
        "schema_version": SEARCH_STATS_SCHEMA_VERSION,
        "last_updated": create_timestamp(),
        "total_searches": 0,
        "total_views": 0,
        "months": {
            # "YYYY-MM": {
            #   searches: 0,
            #   views: 0,
            # }
        },
        "by_property": {},
        "search_token_hits": {},  # token => count (за Top 5 търсени думи/фрази)
    }


def load_search_stats() -> Dict[str, Any]:
    data = _atomic_read_json(STATS_FILE, None)
    if data is None:
        return _default_stats()
    d = _default_stats()
    for k, v in data.items():
        d[k] = v
    d.setdefault("schema_version", SEARCH_STATS_SCHEMA_VERSION)
    d.setdefault("last_updated", create_timestamp())
    d.setdefault("months", {})
    d.setdefault("by_property", {})
    d.setdefault("search_token_hits", {})
    return d


def save_search_stats(stats: Dict[str, Any]) -> None:
    stats["last_updated"] = create_timestamp()
    _atomic_write_json(STATS_FILE, stats)


def _ensure_month(stats: Dict[str, Any], month_key: Optional[str] = None) -> Dict[str, Any]:
    month_key = month_key or _current_month_key()
    months = stats.setdefault("months", {})
    if month_key not in months:
        months[month_key] = {
            "searches": 0,
            "views": 0,
        }
    m = months[month_key]
    for k, v in _default_stats()["months"].get("__template__", {}).items():
        m.setdefault(k, v)
    return m


def _ensure_property(stats: Dict[str, Any], property_id: str) -> Dict[str, Any]:
    props = stats["by_property"]
    if property_id not in props:
        props[property_id] = {
            "view_count": 0,
            "search_hit_count": 0,
            "last_searched_at": None,
            "last_viewed_at": None,
        }
    entry = props[property_id]
    entry.setdefault("view_count", 0)
    entry.setdefault("search_hit_count", 0)
    entry.setdefault("last_searched_at", None)
    entry.setdefault("last_viewed_at", None)
    return entry


def _record_token_hits(stats: Dict[str, Any], q: str) -> None:
    if not q:
        return
    text = q.lower().strip()
    if not text:
        return
    stats.setdefault("search_token_hits", {})
    stats["search_token_hits"][text] = int(stats["search_token_hits"].get(text, 0)) + 1
    tokens = [t for t in text.split() if len(t) >= 2]
    for tok in tokens:
        stats["search_token_hits"][tok] = int(stats["search_token_hits"].get(tok, 0)) + 1


def increment_search_hit(
    property_ids: List[str],
    *,
    query_text: Optional[str] = None,
) -> None:
    stats = load_search_stats()
    ts = create_timestamp()
    month = _ensure_month(stats)

    stats["total_searches"] = int(stats.get("total_searches", 0)) + 1
    month["searches"] = int(month.get("searches", 0)) + 1

    if query_text:
        _record_token_hits(stats, query_text)

    if property_ids:
        for pid in property_ids:
            entry = _ensure_property(stats, pid)
            entry["search_hit_count"] = int(entry.get("search_hit_count", 0)) + 1
            entry["last_searched_at"] = ts

    save_search_stats(stats)


def increment_view(property_id: str) -> None:
    stats = load_search_stats()
    stats["total_views"] = int(stats.get("total_views", 0)) + 1
    entry = _ensure_property(stats, property_id)
    entry["view_count"] = int(entry.get("view_count", 0)) + 1
    entry["last_viewed_at"] = create_timestamp()
    month = _ensure_month(stats)
    month["views"] = int(month.get("views", 0)) + 1
    save_search_stats(stats)


def get_property_popularity(property_id: str) -> Dict[str, Any]:
    stats = load_search_stats()
    props = stats.get("by_property", {})
    if property_id not in props:
        return {"view_count": 0, "search_hit_count": 0}
    e = props[property_id]
    return {
        "view_count": e.get("view_count", 0),
        "search_hit_count": e.get("search_hit_count", 0),
        "last_searched_at": e.get("last_searched_at"),
        "last_viewed_at": e.get("last_viewed_at"),
    }


def get_top_properties(limit: int = 20, by: str = "views") -> List[Dict[str, Any]]:
    stats = load_search_stats()
    rows = []
    for pid, e in stats.get("by_property", {}).items():
        rows.append({
            "property_id": pid,
            "view_count": e.get("view_count", 0),
            "search_hit_count": e.get("search_hit_count", 0),
        })
    key = "view_count" if by == "views" else "search_hit_count"
    rows.sort(key=lambda r: r[key], reverse=True)
    return rows[:limit]


def get_top_search_tokens(limit: int = 10) -> List[Dict[str, Any]]:
    stats = load_search_stats()
    tokens = stats.get("search_token_hits", {})
    rows = [{"token": t, "count": int(c)} for t, c in tokens.items()]
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows[:limit]


def _month_range() -> List[str]:
    out = []
    now = datetime.now(TIMEZONE)
    for m in range(12):
        ref = datetime(now.year, now.month, 1, tzinfo=TIMEZONE)
        # отиваме 13 месеца назад, за да покрием current + 12
        y = ref.year
        mo = (ref.month - m - 1)
        while mo <= 0:
            mo += 12
            y -= 1
        out.append(f"{y:04d}-{mo:02d}")
    out.reverse()
    # Добавяме текущия ако липсва
    cur = _current_month_key()
    if cur not in out:
        out.append(cur)
    return out


def get_summary() -> Dict[str, Any]:
    stats = load_search_stats()
    properties_tracked = len(stats.get("by_property", {}))
    ranked = get_top_properties(10)
    month_key = _current_month_key()
    current_month = stats.get("months", {}).get(month_key, {})
    return {
        "schema_version": stats.get("schema_version"),
        "last_updated": stats.get("last_updated"),
        "total_searches": stats.get("total_searches", 0),
        "total_views": stats.get("total_views", 0),
        "properties_tracked": properties_tracked,
        "top_properties": ranked,
        "top_search_tokens": get_top_search_tokens(5),
        "current_month": {
            "key": month_key,
            **current_month,
        },
        "series": {
            "months": _month_range(),
        },
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Analytics management")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    if args.summary:
        print(json.dumps(get_summary(), ensure_ascii=False, indent=2))
    else:
        top = get_top_properties(args.top)
        print(json.dumps(top, ensure_ascii=False, indent=2))
