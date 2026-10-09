# TAG: SEARCH API + DASHBOARD
# Flask приложение: търсене на нормализирани имоти +
# dashboard за преглед на състоянието на pipeline-а.
#
# ПРОЕКТЪТ Е НЕКОМЕРЦИАЛЕН ХОБИ ПРОЕКТ.
#   Няма потребителски акаунти, няма квоти, няма плащания.
#   Първоначалната версия съдържаше PayPal checkout, кредитна
#   система и DEMO/PROD превключвател - всичко това е премахнато.
#   Търсенето е отворено и безплатно, което е точното за проекта.
#
# НАЛИЧНО:
#  - GET  /api/v1/search         търсене с филтри по цена/площ
#  - GET  /api/v1/properties/<id> детайли на един запис
#  - GET  /api/v1/snapshots     наличните финални снимки
#  - GET  /api/stats            статистика за ползване
#  - GET  /api/settings          настройките
#  - GET  /health                проверка за живост
#  - POST /api/pipeline/run      ръчно пускане на pipeline-а
#  - POST /api/pipeline/cancel   отказ на текущия рън
#  - POST /api/scheduler/set     вкл./изкл. на планировчика

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import ijson

from netpolicy import describe as describe_network


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import analytics as analytics_mod
from tools.finished_exporter import (
    find_finished_snapshots,
    find_latest_finished_snapshot,
)
from tools.matcher import transliterate_text
from tools.settings import (
    get_settings,
    save_settings,
    for_frontend,
)
from tools.pipeline_scheduler import worker as pipeline_worker


def _ensure_flask():
    try:
        from flask import Flask, jsonify, request, Response
        return Flask, jsonify, request, Response
    except ImportError as e:
        print("[ERROR] Flask not installed. Run: pip install flask")
        raise e


Flask, jsonify, request, Response = _ensure_flask()


app = Flask(
    __name__,
    static_folder=str(PROJECT_ROOT / "storage"),
)


# ============================================================
# Init - стартира scheduler ако е включен (Flask 3.x compatible)
# ============================================================

_triggered_startup = {"v": False}

@app.before_request
def _trigger_once_init():
    if not _triggered_startup["v"]:
        _triggered_startup["v"] = True
        try:
            pipeline_worker.ensure_started()
        except Exception:
            pass


# ============================================================
# HELPERS
# ============================================================

def parse_params(params: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and v == "":
            continue
        out[k] = v
    return out


_loaded_snapshot: Optional[List[Dict[str, Any]]] = None
_loaded_snapshot_path: Optional[str] = None


def load_snapshot_records(snapshot_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    global _loaded_snapshot, _loaded_snapshot_path
    snap_path = snapshot_path or find_latest_finished_snapshot()
    if not snap_path:
        return []
    props_file = snap_path / "finished_properties.json"
    if not props_file.exists():
        return []
    key = str(props_file) + "|" + str(props_file.stat().st_mtime if props_file.exists() else 0)
    if _loaded_snapshot is not None and _loaded_snapshot_path == key:
        return _loaded_snapshot
    with props_file.open("rb") as f:
        recs = list(ijson.items(f, "item", use_float=True))
    _loaded_snapshot = recs
    _loaded_snapshot_path = key
    return recs


def record_to_searchable_text(r: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k, v in (r.get("attributes") or {}).items():
        if isinstance(v, str):
            parts.append(v.lower())
        elif isinstance(v, (int, float)):
            parts.append(str(v))
    name = r.get("property_id") or ""
    parts.append(str(name).lower())
    cluster = r.get("_cluster") or {}
    rep = cluster.get("representative") or ""
    parts.append(str(rep).lower())
    return " ".join(parts)


def build_search_blob(r: Dict[str, Any]) -> str:
    """
    Търсеният текст, по който се филтрира запис.

    Освен оригинала (кирилица) се добавя и РОМАНИЗИРАН
    вариант. Без него агент, който пита "Lyulin" или
    "Levski", не намира нищо, защото данните са на
    български. Продуктът е насочен към чуждоезични
    агенти, така че това не е козметика.
    """

    original = record_to_searchable_text(r)

    if not any(
        "\u0400" <= ch <= "\u04ff"
        for ch in original
    ):
        return original

    return (
        original
        + " "
        + transliterate_text(original)
    )


def search_records(
    records: List[Dict[str, Any]],
    q: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_area: Optional[float] = None,
    max_area: Optional[float] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    # За всеки токен от заявката се пази списък от варианти
    # (оригинал + романизиран). Търсенето е AND: всеки токен
    # трябва да съвпадне с поне един от своите варианти.
    # Така "lyulin" намира "ж.к. Люлин" и обратно.
    token_variants: List[List[str]] = []


    for raw_token in (q or "").lower().split():
        if not raw_token:
            continue
        variants = [raw_token]
        latin = transliterate_text(raw_token)
        if latin and latin != raw_token:
            variants.append(latin)
        token_variants.append(variants)

    results = []
    for r in records:
        price = r.get("price")
        area = r.get("area_m2")
        if min_price is not None and (price is None or price < min_price):
            continue
        if max_price is not None and (price is None or price > max_price):
            continue
        if min_area is not None and (area is None or area < 
 min_area):
            continue
        if max_area is not None and (area is None or area > max_area):
            continue
        if token_variants:
            haystack = build_search_blob(r)
            if not all(
                any(variant in haystack for variant in variants)
                for variants in token_variants
            ):
                continue
        results.append(r)
    total = len(results)
    page = results[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "results": page}


def fmt_eur(v):
    if v is None:
        return "€ 0.00"
    return f"€ {float(v):,.2f}"


# ============================================================
# SETTINGS / MODE
# ============================================================

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({
        "settings": for_frontend(),
        "network": describe_network(),
    })


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    payload = request.get_json(force=True, silent=True) or {}
    # Allow only selected fields (no arbitrary injection)
    allowed = {
        "schedule", "query_log_limit", "debug_logs", "fetch",
    }
    safe_update = {k: v for k, v in payload.items() if k in allowed}
    save_settings(safe_update)
    return jsonify({
        "ok": True,
        "settings": for_frontend(),
        "network": describe_network(),
    })


# ============================================================
# PIPELINE / SCHEDULER
# ============================================================

@app.route("/api/pipeline/status", methods=["GET"])
def api_pipeline_status():
    return jsonify(pipeline_worker.snapshot())


@app.route("/api/pipeline/run", methods=["POST"])
def api_pipeline_run():
    payload = request.get_json(force=True, silent=True) or {}
    # collect/fetch_all са по подразбиране изключени: мрежата
    # е изключена в този билд, а планировчикът ги пропуска
    # сам, ако някой ги помоли.
    options = {
        "setup": bool(payload.get("setup", False)),
        "collect": bool(payload.get("collect", False)),
        "fetch_all": bool(payload.get("fetch_all", False)),
        "fetch_ids": list(payload.get("fetch_ids", []) or []),
        "fresh_matcher": bool(payload.get("fresh_matcher", False)),
    }
    started = pipeline_worker.run_manual(options)
    return jsonify({"started": started, "status": pipeline_worker.snapshot()})


@app.route("/api/pipeline/cancel", methods=["POST"])
def api_pipeline_cancel():
    pipeline_worker.cancel()
    return jsonify({"ok": True, "status": pipeline_worker.snapshot()})


@app.route("/api/scheduler/set", methods=["POST"])
def api_scheduler_set():
    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    every_hours = max(1, int(payload.get("every_hours", 12)))
    pipeline_worker.set_auto(enabled=enabled, every_hours=every_hours)
    return jsonify({"ok": True, "status": pipeline_worker.snapshot()})



# ============================================================
# SEARCH ENDPOINT
# ============================================================

@app.route("/api/v1/search", methods=["GET", "POST"])
def api_search():
    t0 = time.time()

    if request.method == "POST":
        p = request.get_json(force=True, silent=True) or {}
    else:
        p = request.args.to_dict()
    params = parse_params(p)
    snap_name = params.get("snapshot")
    snap_dir = None
    if snap_name:
        for s in find_finished_snapshots():
            if s.name == snap_name:
                snap_dir = s
                break

    records = load_snapshot_records(snap_dir)
    query_text = params.get("q", "")
    result = search_records(
        records,
        q=query_text,
        min_price=float(params["min_price"]) if params.get("min_price") else None,
        max_price=float(params["max_price"]) if params.get("max_price") else None,
        min_area=float(params["min_area"]) if params.get("min_area") else None,
        max_area=float(params["max_area"]) if params.get("max_area") else None,
        limit=min(int(params.get("limit", 50)), 500),
        offset=int(params.get("offset", 0)),
    )

    hit_ids = [r["property_id"] for r in result["results"]]
    if hit_ids:
        analytics_mod.increment_search_hit(
            hit_ids, query_text=query_text,
        )

    duration_ms = round((time.time() - t0) * 1000, 1)

    latest = find_latest_finished_snapshot()
    response = {
        "snapshot": (
            snap_dir.name if snap_dir else (latest.name if latest else None)
        ),
        "duration_ms": duration_ms,
        **result,
    }
    return jsonify(response)


# ============================================================
# PROPERTY DETAIL
# ============================================================

@app.route("/api/v1/properties/<property_id>", methods=["GET"])
def api_property_detail(property_id: str):
    records = load_snapshot_records()
    for r in records:
        if r.get("property_id") == property_id:
            analytics_mod.increment_view(property_id)
            return jsonify(r)
    return jsonify({"error": "NOT_FOUND"}), 404


# ============================================================
# SNAPSHOTS / STATS / HEALTH
# ============================================================

@app.route("/api/v1/snapshots", methods=["GET"])
def api_snapshots():
    snaps = find_finished_snapshots()
    out = []
    for s in snaps:
        info: Dict[str, Any] = {"name": s.name, "path": str(s)}
        mf = s / "metadata.json"
        if mf.exists():
            try:
                info["metadata"] = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                pass
        out.append(info)
    out.reverse()
    return jsonify({"count": len(out), "snapshots": out})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    snap = find_latest_finished_snapshot()
    snap_meta = None
    snap_name = None
    if snap:
        snap_name = snap.name
        mf = snap / "metadata.json"
        if mf.exists():
            try:
                snap_meta = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                pass
    return jsonify({
        "snapshot": {
            "name": snap_name,
            "metadata": snap_meta,
        },
        "analytics": analytics_mod.get_summary(),
        "pipeline": pipeline_worker.snapshot(),
        "top_properties": analytics_mod.get_top_properties(20),
        "top_search_tokens": analytics_mod.get_top_search_tokens(5),
        "settings": for_frontend(),
        "network": describe_network(),
    })


@app.route("/health", methods=["GET"])
def health():
    snap = find_latest_finished_snapshot()
    return jsonify({
        "status": "ok",
        "network": describe_network(),
        "latest_finished_snapshot": snap.name if snap else None,
        "has_finished_data": bool(snap and (snap / "finished_properties.json").exists()),
        "pipeline": pipeline_worker.snapshot(),
    })


# ============================================================
# DASHBOARD UI
# ============================================================

# TAG: DASHBOARD UI
#
# Само за преглед на състоянието. Проектът е некомерсиален:
# няма акаунти, няма квоти, няма настройки за пари.
#
# Тук НЕ се ползва външен CSS/JS framework - единствените
# външни ресурси са шрифтът и Bootstrap Icons, и дори те са
# незадължителни (страницата работи и без интернет).
#
# Съдържанието е вградено в Python като сурова строка, за да
# няма отделен template файл, който да се разминава с кода.

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="bg">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AI Property Market</title>
<style>
  :root {
    --bg: #f4f6fb;
    --card: #ffffff;
    --ink: #14183a;
    --muted: #667;
    --line: #e2e6f0;
    --accent: #2f4bd8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  header {
    background: linear-gradient(135deg, #0f1535, #2b3f9e);
    color: #fff; padding: 22px 28px;
  }
  header h1 { margin: 0; font-size: 1.35rem; letter-spacing: -0.01em; }
  header p  { margin: 4px 0 0; opacity: .8; font-size: .88rem; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 22px 20px 60px; }
  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
  .card {
    background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 15px 17px;
  }
  .card h3 {
    margin: 0 0 8px; font-size: .74rem; text-transform: uppercase;
    letter-spacing: .07em; color: var(--muted); font-weight: 700;
  }
  .val { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
  .sub { font-size: .78rem; color: var(--muted); margin-top: 3px; }
  section { margin-top: 26px; }
  section > h2 {
    font-size: .95rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin: 0 0 10px; font-weight: 700;
  }
  table { width: 100%; border-collapse: collapse; font-size: .87rem; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
  th { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
  tr:last-child td { border-bottom: 0; }
  code, .mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: .84em; }
  .pill {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: .72rem; font-weight: 700; letter-spacing: .04em;
  }
  .pill-ok   { background: #e4f6ec; color: #14663c; }
  .pill-warn { background: #fdf0d5; color: #7a5a00; }
  .pill-off  { background: #eceff7; color: #4a5170; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  button {
    font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer;
    border: 1px solid var(--accent); background: var(--accent); color: #fff;
    padding: 7px 15px; border-radius: 8px;
  }
  button.ghost { background: transparent; color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .empty { color: var(--muted); font-style: italic; padding: 10px 0; font-size: .87rem; }
  footer { margin-top: 34px; font-size: .78rem; color: var(--muted); text-align: center; }
  a { color: var(--accent); }
</style>
</head>
<body>

<header>
  <h1>AI Property Market</h1>
  <p>Некомерсиален хоби проект &middot; общност: <b>Георги Иванов Кирев</b> &middot; Хасково, България</p>
</header>

<div class="wrap">

  <div class="grid">
    <div class="card">
      <h3>Снимка</h3>
      <div class="val" id="mSnapshot">-</div>
      <div class="sub" id="mSnapshotMeta">-</div>
    </div>
    <div class="card">
      <h3>Имоти</h3>
      <div class="val" id="mProps">-</div>
      <div class="sub">в последната снимка</div>
    </div>
    <div class="card">
      <h3>Търсения</h3>
      <div class="val" id="mSearches">-</div>
      <div class="sub" id="mViews">- прегледа</div>
    </div>
    <div class="card">
      <h3>Мрежа</h3>
      <div class="val"><span class="pill pill-off" id="mNet">-</span></div>
      <div class="sub" id="mNetNote">-</div>
    </div>
  </div>

  <section>
    <h2>Pipeline</h2>
    <div class="card">
      <div class="row">
        <button id="bRun">Стартирай локален pipeline</button>
        <button class="ghost" id="bCancel">Отказ</button>
        <span class="sub" id="pStat">-</span>
      </div>
      <div class="sub" id="pLog" style="margin-top:8px"></div>
    </div>
  </section>

  <section>
    <h2>Най-търсени имоти</h2>
    <div class="card">
      <table>
        <thead><tr><th>Имот</th><th class="mono">property_id</th><th>Цена</th><th>Площ</th><th>Показвания</th></tr></thead>
        <tbody id="topBody"><tr><td colspan="5" class="empty">няма данни</td></tr></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Най-чести търсени думи</h2>
    <div class="card">
      <table>
        <thead><tr><th>Дума / фраза</th><th>Пъти</th></tr></thead>
        <tbody id="tokBody"><tr><td colspan="2" class="empty">няма данни</td></tr></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Налични снимки</h2>
    <div class="card">
      <table>
        <thead><tr><th>Име</th><th>Записи</th><th>С цена</th><th>С площ</th></tr></thead>
        <tbody id="snapBody"><tr><td colspan="4" class="empty">няма данни</td></tr></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>API</h2>
    <div class="card">
      <table>
        <thead><tr><th>Метод</th><th>Път</th><th>Какво прави</th></tr></thead>
        <tbody>
          <tr><td class="mono">GET</td><td class="mono"><a href="/api/v1/search">/api/v1/search</a></td><td>търсене (q, min_price, max_price, min_area, max_area, limit, offset)</td></tr>
          <tr><td class="mono">GET</td><td class="mono"><a href="/api/v1/snapshots">/api/v1/snapshots</a></td><td>наличните финални снимки</td></tr>
          <tr><td class="mono">GET</td><td class="mono"><a href="/api/stats">/api/stats</a></td><td>статистика за ползване</td></tr>
          <tr><td class="mono">GET</td><td class="mono"><a href="/health">/health</a></td><td>проверка за живост</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <footer>
    Hobby project, non-commercial. Built with AI assistance. No network access required.
  </footer>
</div>

<script>
const $ = (id) => document.getElementById(id);

function num(n) { return (n === null || n === undefined) ? "-" : Number(n).toLocaleString("bg-BG"); }
function eur(n) { return (n === null || n === undefined) ? "-" : Number(n).toLocaleString("bg-BG", {minimumFractionDigits: 2}) + " EUR"; }

function fill(tbodyId, rows, cols, emptyText) {
  const tb = $(tbodyId);
  if (!rows || !rows.length) {
    tb.innerHTML = '<tr><td colspan="' + cols + '" class="empty">' + emptyText + '</td></tr>';
    return;
  }
  tb.innerHTML = rows.join("");
}

async function loadStats() {
  let s;
  try { s = await (await fetch("/api/stats")).json(); }
  catch (e) { return; }

  const snap = s.snapshot || {};
  const meta = snap.metadata || {};
  const an = s.analytics || {};

  $("mSnapshot").textContent = snap.name || "няма";
  $("mSnapshotMeta").textContent = meta.properties_with_price !== undefined
    ? ("с цена: " + num(meta.properties_with_price))
    : "-";
  $("mProps").textContent = num(meta.total_properties);
  $("mSearches").textContent = num(an.total_searches);
  $("mViews").textContent = num(an.total_views) + " прегледа";

  const net = s.network || "";
  const on = net.indexOf("ENABLED") === 0;
  $("mNet").textContent = on ? "включена" : "изключена";
  $("mNet").className = "pill " + (on ? "pill-warn" : "pill-off");
  $("mNetNote").textContent = on ? "ще сваля от външни източници" : "работи само с локалните данни";

  fill("topBody", (s.top_properties || []).map(p => {
    const price = (p.price === null || p.price === undefined) ? "-" : eur(p.price);
    const area = (p.area_m2 === null || p.area_m2 === undefined) ? "-" : num(p.area_m2) + " m2";
    return "<tr><td>" + (p.label || p.property_id || "-") + "</td>" +
           "<td class=\"mono\">" + (p.property_id || "-") + "</td>" +
           "<td>" + price + "</td><td>" + area + "</td>" +
           "<td>" + num(p.view_count) + "</td></tr>";
  }), 5, "няма прегледи още");

  fill("tokBody", (s.top_search_tokens || []).map(t =>
    "<tr><td class=\"mono\">" + (t.token || "-") + "</td><td>" + num(t.count) + "</td></tr>"
  ), 2, "няма търсения още");

  const p = s.pipeline || {};
  $("pStat").textContent = p.running ? ("Върви: " + (p.step || "...")) : (p.last_status || "idle");
  $("pLog").textContent = (p.last_log || "").slice(-400);
  $("bRun").disabled = !!p.running;
  $("bCancel").disabled = !p.running;
}

async function loadSnapshots() {
  let s;
  try { s = await (await fetch("/api/v1/snapshots")).json(); }
  catch (e) { return; }
  fill("snapBody", (s.snapshots || []).map(x => {
    const m = x.metadata || {};
    return "<tr><td class=\"mono\">" + (x.name || "-") + "</td>" +
           "<td>" + num(m.total_properties) + "</td>" +
           "<td>" + num(m.properties_with_price) + "</td>" +
           "<td>" + num(m.properties_with_area) + "</td></tr>";
  }), 4, "няма снимки - стартирай pipeline-а");
}

$("bRun").onclick = async () => {
  $("bRun").disabled = true;
  await fetch("/api/pipeline/run", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({setup: false})
  });
  loadStats();
};
$("bCancel").onclick = async () => {
  await fetch("/api/pipeline/cancel", {method: "POST"});
  loadStats();
};

loadStats();
loadSnapshots();
setInterval(loadStats, 5000);
</script>

</body>
</html>
"""


@app.route("/", methods=["GET"])
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html; charset=utf-8")


def create_app():
    return app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Start Search API + Dashboard (Flask)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Инициализирай scheduler преди да слушаме
    pipeline_worker.ensure_started()

    snap = find_latest_finished_snapshot()

    print(f"[API] Starting at http://{args.host}:{args.port}")
    print(f"[API] Dashboard    : http://{args.host}:{args.port}/")
    print(f"[API] Health       : http://{args.host}:{args.port}/health")
    print(f"[API] Search       : GET http://{args.host}:{args.port}/api/v1/search")
    print(f"[API] Network      : {describe_network()}")
    print(f"[API] Data         : {snap.name if snap else 'none - run: python main.py --pipeline'}")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
