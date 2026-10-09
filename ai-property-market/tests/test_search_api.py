"""
Тестове за tools/search_api.py (Flask).

Използва Flask test client - реален сървър не се вдига.

API-то е ОТВОРЕНО: няма акаунти, няма токени, няма квоти.
Проектът е некомерсиален хоби проект и плащането е
премахнато напълно - виж README.md, "Какво НЕ е тук".

Поради това тук няма тестове за автентикация и права:
няма какво да се проверява. На тяхно място стои проверката,
че старите търговски endpoints наистина са ИЗТРЪГНАТИ -
връщането им би означавало, че премахването е само козметично.
"""

import json
from pathlib import Path

import pytest


# ============================================================
# TAG: FIXTURES
# ============================================================

@pytest.fixture
def api(tmp_path, monkeypatch):
    from tools import search_api as mod
    from tools import settings as settings_mod
    from tools import finished_exporter as fin

    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        mod, "PROJECT_ROOT", tmp_path
    )
    monkeypatch.setattr(
        fin, "FINISHED_DIR", storage / "finished"
    )
    monkeypatch.setattr(
        mod.analytics_mod, "ANALYTICS_DIR",
        storage / "analytics"
    )
    monkeypatch.setattr(
        mod.analytics_mod, "STATS_FILE",
        storage / "analytics" / "search_stats.json"
    )
    monkeypatch.setattr(
        settings_mod, "STORAGE_DIR", storage
    )
    monkeypatch.setattr(
        settings_mod, "SETTINGS_FILE",
        storage / "settings.json"
    )

    mod._loaded_snapshot = None
    mod._loaded_snapshot_path = None
    fin._FINISHED_INDEX_CACHE.clear()

    yield mod

    mod._loaded_snapshot = None
    mod._loaded_snapshot_path = None
    fin._FINISHED_INDEX_CACHE.clear()


@pytest.fixture
def client(api):
    api.app.config.update(TESTING=True)

    with api.app.test_client() as c:
        yield c


@pytest.fixture
def finished_snapshot(api, tmp_path):
    """
    Създава валиден finished snapshot с 2 имота.
    """

    from tools.finished_exporter import FINISHED_DIR

    snapshot = FINISHED_DIR / "01-01-2026_10"
    snapshot.mkdir(parents=True, exist_ok=True)

    records = [
        {
            "property_id": "prop_aaa111",
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {
                "regname": "ж.к. Люлин - 1 мр",
                "kvartal": "Люлин - 1",
                "cena_ap": 11722.96
            },
            "location": {
                "geometry": {
                    "type": "Point",
                    "coordinates": [23.4, 42.7]
                }
            },
            "price": 11722.96,
            "area_m2": 54314.5,
            "price_history": {
                "current_price": 11722.96,
                "delta_1w": None,
                "delta_3m": None,
                "delta_6m": None,
                "delta_1y": None,
                "delta_all": None,
                "last_updated": "01-01-2026_10"
            },
            "popularity": {
                "view_count": 5,
                "search_hit_count": 2
            },
            "_snapshot_timestamp": "01-01-2026_10"
        },
        {
            "property_id": "prop_bbb222",
            "source": "sofiaplan",
            "dataset_id": 624,
            "attributes": {
                "regname": "ЦГЧ Зона А - север",
                "kvartal": "ЦГЧ",
                "cena_ap": 116813.08
            },
            "location": {
                "geometry": {
                    "type": "Point",
                    "coordinates": [23.32, 42.69]
                }
            },
            "price": 116813.08,
            "area_m2": 98225.38,
            "price_history": {
                "current_price": 116813.08,
                "delta_1w": None,
                "delta_3m": None,
                "delta_6m": None,
                "delta_1y": None,
                "delta_all": None,
                "last_updated": "01-01-2026_10"
            },
            "popularity": {
                "view_count": 1,
                "search_hit_count": 0
            },
            "_snapshot_timestamp": "01-01-2026_10"
        }
    ]

    (snapshot / "finished_properties.json").write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8"
    )

    (snapshot / "metadata.json").write_text(
        json.dumps({
            "timestamp": "01-01-2026_10",
            "total_properties": 2,
            "properties_with_price": 2,
            "properties_with_area": 2
        }),
        encoding="utf-8"
    )

    api._loaded_snapshot = None
    api._loaded_snapshot_path = None

    return snapshot


# ============================================================
# TAG: BASIC ROUTES
# ============================================================

def test_index_loads(client):
    response = client.get("/")

    assert response.status_code == 200
    assert len(response.data) > 0


def test_index_contains_no_payment_ui(client):
    """
    РЕГРЕСИЯ: dashboard-ът беше пълен с PayPanel настройки,
    полета за client_secret и имейл на търговец. Всичко това
    е премахнато - и dashboard-ът вече не трябва да го
    споменава дори като текст.
    """

    body = client.get("/").get_data(as_text=True).lower()

    for banned in ("paypal", "client_secret", "merchant", "checkout"):
        assert banned not in body, (
            f"dashboard-ът все още споменава {banned!r}"
        )


def test_health(client, finished_snapshot):
    response = client.get("/health")

    assert response.status_code == 200

    payload = response.get_json()

    assert payload["status"] == "ok"
    assert payload["has_finished_data"] is True
    assert payload["latest_finished_snapshot"] == "01-01-2026_10"


def test_health_without_data(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["has_finished_data"] is False


def test_health_reports_network_policy(client):
    """
    /health казва открито дали свалянето на данни е разрешено.
    """

    payload = client.get("/health").get_json()

    assert "DISABLED" in payload["network"]


def test_health_network_is_disabled_unless_overridden(
    client,
    monkeypatch
):
    monkeypatch.setenv("AI_PROPERTY_ALLOW_NETWORK", "1")

    payload = client.get("/health").get_json()

    assert "ENABLED" in payload["network"]


def test_stats(client, finished_snapshot):
    response = client.get("/api/stats")

    assert response.status_code == 200

    payload = response.get_json()

    assert "snapshot" in payload
    assert payload["snapshot"]["name"] == "01-01-2026_10"


def test_stats_has_no_commercial_fields(client):
    """
    РЕГРЕСИЯ: /api/stats връщаше price_per_search_eur,
    free_quota_per_new_agent, lifetime revenue и agents.
    Проектът вече не продава нищо - тези полета няма какво
    да означават и само объркват потребителя на API-то.
    """

    payload = client.get("/api/stats").get_json()

    for banned in (
        "price_per_search_eur",
        "free_quota_per_new_agent",
        "lifetime",
        "agents",
        "agents_list",
        "top_agents",
        "recent_queries",
        "paypal_status",
        "mode",
    ):
        assert banned not in payload, (
            f"/api/stats все още връща {banned!r}"
        )


def test_settings_get(client):
    response = client.get("/api/settings")

    assert response.status_code == 200
    assert isinstance(response.get_json(), dict)


def test_settings_have_no_paypal_block(client):
    payload = client.get("/api/settings").get_json()

    assert "paypal" not in payload["settings"]
    assert "pricing" not in payload["settings"]
    assert "mode" not in payload["settings"]


def test_settings_post_ignores_commercial_keys(client):
    """
    РЕГРЕСИЯ: POST /api/settings приемаше 'paypal' и 'mode'
    и ги записваше в storage/settings.json. Дори да няма какво
    да се запише, приемането на такива ключове е вход, който
    утре може да се разшири.
    """

    response = client.post(
        "/api/settings",
        json={
            "paypal": {"client_id": "x", "client_secret": "y"},
            "mode": "prod",
            "pricing": {"price_per_search_eur": 1},
            "query_log_limit": 5,
        }
    )

    assert response.status_code == 200

    saved = response.get_json()["settings"]

    assert "paypal" not in saved
    assert "mode" not in saved
    assert "pricing" not in saved
    assert saved["query_log_limit"] == 5


def test_snapshots(client, finished_snapshot):
    response = client.get("/api/v1/snapshots")

    assert response.status_code == 200
    assert isinstance(response.get_json(), (list, dict))


# ============================================================
# TAG: Т�РГОВСКИТЕ ENDPOINTS СА ИЗТРЪГНАТИ
# ============================================================

@pytest.mark.parametrize("path", [
    "/api/v1/agents/register",
    "/api/v1/paypal/status",
    "/api/v1/paypal/create-order",
    "/api/v1/paypal/capture-order",
    "/api/mode/toggle",
    "/paypal/return",
    "/paypal/cancel",
])
def test_removed_routes_are_gone(client, path):
    """
    РЕГРЕСИЯ: премахването на плащанията трябва да е
    истинско. Ако някой ден някой върне endpoint-а, тези
    тестове падат.
    """

    assert client.post(path).status_code == 404
    assert client.get(path).status_code == 404


def test_agent_routes_are_gone_by_id(client):
    assert client.get("/api/v1/agents/agt_x/info").status_code == 404
    assert client.post("/api/v1/agents/agt_x/credits").status_code == 404


# ============================================================
# TAG: SEARCH
# ============================================================

def test_search_needs_no_token(client, finished_snapshot):
    """
    Търсенето е отворено. Токен не се изисква и не се чете -
    header-ът се игнорира, а липсата му не е грешка.
    """

    response = client.get("/api/v1/search")

    assert response.status_code == 200
    assert response.get_json()["total"] == 2


def test_search_ignores_a_stale_agent_token(client, finished_snapshot):
    """
    Стари клиенти може да още изпращат X-Agent-Token.
    Той просто се игнорира - не се 401-ва.
    """

    response = client.get(
        "/api/v1/search",
        headers={"X-Agent-Token": "agt_legacy_dead_token"}
    )

    assert response.status_code == 200


def test_search_response_has_no_billing_fields(client, finished_snapshot):
    payload = client.get("/api/v1/search").get_json()

    for banned in ("agent_id", "tier", "charged_eur", "mode"):
        assert banned not in payload


def test_search_query_filter(client, finished_snapshot):
    response = client.get("/api/v1/search?q=Lyulin")

    assert response.status_code == 200

    results = (
        response.get_json().get("results")
        or []
    )

    assert len(results) == 1, (
        "търсенето трябва да намери ЗОНАТА с име Люлин"
    )

    blob = json.dumps(
        results[0], ensure_ascii=False
    ).lower()

    assert "люлин" in blob


def test_search_cyrillic_query(client, finished_snapshot):
    response = client.get("/api/v1/search?q=ЦГЧ")

    assert response.status_code == 200

    results = (
        response.get_json().get("results")
        or []
    )

    assert len(results) == 1
    assert "цгч" in json.dumps(
        results[0], ensure_ascii=False
    ).lower()


def test_search_no_query_returns_all(client, finished_snapshot):
    response = client.get("/api/v1/search")

    assert response.status_code == 200

    payload = response.get_json()

    assert payload["total"] == 2
    assert len(payload["results"]) == 2


def test_search_unknown_query_returns_empty(client, finished_snapshot):
    """
    Празен резултат е валиден - просто няма такъв запис.
    """

    response = client.get(
        "/api/v1/search?q=zzz_nonexistent_term_12345"
    )

    assert response.status_code == 200
    assert response.get_json()["total"] == 0


def test_search_price_filter(client, finished_snapshot):
    response = client.get("/api/v1/search?min_price=50000")

    assert response.status_code == 200

    payload = response.get_json()

    results = (
        payload.get("results")
        or payload.get("properties")
        or []
    )

    assert len(results) == 1
    assert results[0]["price"] >= 50000


def test_search_max_price_filter(client, finished_snapshot):
    response = client.get("/api/v1/search?max_price=50000")

    assert response.status_code == 200

    results = (
        response.get_json().get("results")
        or response.get_json().get("properties")
        or []
    )

    assert len(results) == 1
    assert results[0]["price"] <= 50000


def test_search_area_filter(client, finished_snapshot):
    response = client.get("/api/v1/search?min_area=60000")

    assert response.status_code == 200

    results = (
        response.get_json().get("results")
        or []
    )

    assert len(results) == 1
    assert results[0]["area_m2"] >= 60000


def test_search_pagination(client, finished_snapshot):
    response = client.get("/api/v1/search?limit=1")

    assert response.status_code == 200

    payload = response.get_json()

    assert payload["total"] == 2
    assert len(payload["results"]) == 1
    assert payload["limit"] == 1


def test_search_accepts_post(client, finished_snapshot):
    response = client.post(
        "/api/v1/search",
        json={"q": "ЦГЧ"}
    )

    assert response.status_code == 200
    assert response.get_json()["total"] == 1


def test_search_returns_numbers_not_strings(
    client,
    finished_snapshot
):
    """
    РЕГРЕСИЯ: search_api чете файла с ijson. Без
    use_float=True всички числа ставаха Decimal/string
    и филтрите по цена/площ мълчаха.
    """

    results = (
        client.get("/api/v1/search").get_json().get("results")
        or []
    )

    assert results, "трябва да има поне един резултат"

    for item in results:
        assert item["price"] is None or isinstance(
            item["price"], (int, float)
        )
        assert item["area_m2"] is None or isinstance(
            item["area_m2"], (int, float)
        )


def test_search_without_data_is_empty_not_error(client):
    response = client.get("/api/v1/search")

    assert response.status_code == 200
    assert response.get_json()["total"] == 0


def test_search_reports_snapshot_name(client, finished_snapshot):
    payload = client.get("/api/v1/search").get_json()

    assert payload["snapshot"] == "01-01-2026_10"


def test_search_by_named_snapshot(client, finished_snapshot):
    payload = client.get(
        "/api/v1/search?snapshot=01-01-2026_10"
    ).get_json()

    assert payload["total"] == 2


def test_search_unknown_snapshot_falls_back_to_latest(
    client,
    finished_snapshot
):
    """
    Непознато име на снимка не бива да връща 500 или празно -
    просто се ползва последната налична.
    """

    response = client.get(
        "/api/v1/search?snapshot=does_not_exist"
    )

    assert response.status_code == 200
    assert response.get_json()["total"] == 2


def test_property_by_id(client, finished_snapshot):
    response = client.get("/api/v1/properties/prop_aaa111")

    assert response.status_code == 200

    payload = response.get_json()

    blob = json.dumps(payload, ensure_ascii=False)

    assert "prop_aaa111" in blob


def test_property_by_id_not_found(client, finished_snapshot):
    response = client.get(
        "/api/v1/properties/prop_does_not_exist"
    )

    assert response.status_code == 404


# ============================================================
# TAG: ANALYTICS
# ============================================================

def test_search_records_analytics(client, finished_snapshot):
    client.get("/api/v1/search")

    summary = client.get("/api/stats").get_json()["analytics"]

    assert summary["total_searches"] == 1


def test_property_view_is_counted(client, finished_snapshot):
    client.get("/api/v1/properties/prop_aaa111")

    summary = client.get("/api/stats").get_json()["analytics"]

    assert summary["total_views"] == 1


def test_analytics_has_no_revenue_fields(client, finished_snapshot):
    client.get("/api/v1/search")

    payload = client.get("/api/stats").get_json()["analytics"]

    assert "lifetime" not in payload
    assert "price_per_search_eur" not in payload