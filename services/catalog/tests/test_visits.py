"""Afluencia de visitantes: registro, estadísticas, predicción y datos de demostración."""

from datetime import timedelta

import pytest

from app import visits

STATS = "/api/v1/visits/stats"
VELO = "catarata-velo-de-las-ninfas"

@pytest.fixture()
def demo(app, seeded):
    with app.app_context():
        assert visits.seed_demo_visits() > 4000
    return seeded

def test_stats_require_a_session(client):
    assert client.get(STATS).status_code == 401
    assert client.get("/api/v1/visits/regional").status_code == 401

def test_without_history_there_is_no_forecast_but_a_clear_message(seeded, user):
    body = seeded.get(STATS, headers=user).get_json()
    assert body["forecast"] == [] and body["model"]["method"] is None and "historial" in body["model"]["message"]
    assert body["quality"] == {"has_demo": False, "has_real": False, "demo_days": 0, "real_days": 0, "mixed_days": 0,
                               "first_date": None, "last_date": None}

def test_seed_demo_is_idempotent_and_labelled(app, demo, user):
    with app.app_context():
        assert visits.seed_demo_visits() == 0
    q = demo.get(STATS, headers=user).get_json()["quality"]
    assert q["has_demo"] and not q["has_real"] and q["demo_days"] > 1000

def test_daily_view_has_sixty_days_of_history_and_fourteen_of_forecast(demo, user):
    body = demo.get(f"{STATS}?granularity=day", headers=user).get_json()
    assert len(body["series"]) == 60 and len(body["forecast"]) == 14
    assert body["series"][-1]["partial"] is True and body["series"][-1]["visitors"] is None   # hoy aún no hay dato
    assert all(f["low"] <= f["expected"] <= f["high"] for f in body["forecast"])
    assert body["model"]["method"] == "diario" and 0 < body["model"]["backtest_mape"] < 40
    k = body["kpis"]
    assert k["last_7"] > 0 and k["last_30"] > k["last_7"] and k["next_7"] > 0 and k["next_30"] > k["next_7"]

def test_monthly_view_has_three_years_and_a_twelve_month_forecast(demo, user):
    body = demo.get(f"{STATS}?granularity=month", headers=user).get_json()
    assert len(body["series"]) == 36 and len(body["forecast"]) == 12
    assert body["forecast"][0]["period"] == body["series"][-1]["period"]        # el mes en curso se pronostica completo
    assert body["model"]["method"] == "holt-winters" and body["model"]["backtest_mape"] < 40

def test_yearly_view_projects_this_year_and_the_next(demo, user):
    body = demo.get(f"{STATS}?granularity=year", headers=user).get_json()
    assert [f["period"] for f in body["forecast"]] == [str(visits.today_pe().year), str(visits.today_pe().year + 1)]
    assert body["series"][-1]["partial"] is True and body["forecast"][0]["expected"] > body["series"][-1]["visitors"] * 0.9

def test_one_place_adds_up_to_less_than_all_places(demo, user):
    total = demo.get(f"{STATS}?granularity=month", headers=user).get_json()["series"][-2]["visitors"]
    one = demo.get(f"{STATS}?granularity=month&place={VELO}", headers=user).get_json()
    assert one["place"]["slug"] == VELO and 0 < one["series"][-2]["visitors"] < total

def test_parameters_are_validated(demo, user):
    assert demo.get(f"{STATS}?granularity=week", headers=user).status_code == 422
    assert demo.get(f"{STATS}?place=no-existe", headers=user).status_code == 404
    assert demo.get(f"{STATS}?place=../x", headers=user).status_code == 422
    assert demo.get(f"{STATS}?granularity=day&rain=hoy:mucha", headers=user).status_code == 422

def test_rain_forecast_lowers_the_expected_visitors_of_that_day(demo, user):
    base = demo.get(f"{STATS}?granularity=day", headers=user).get_json()["forecast"]
    day = base[2]["period"]
    wet = demo.get(f"{STATS}?granularity=day&rain={day}:100", headers=user).get_json()["forecast"]
    assert wet[2]["expected"] < base[2]["expected"] and wet[2]["rain"] == 100.0
    assert wet[3]["expected"] == base[3]["expected"] and base[2]["rain"] is None

def test_only_admins_register_visits_and_a_real_record_replaces_the_demo_one(app, demo, user, admin):
    day = (visits.today_pe() - timedelta(days=3)).isoformat()
    body = {"place": VELO, "date": day, "visitors": 123}
    assert demo.post("/api/v1/admin/visits", json=body, headers=user).status_code == 403
    assert demo.post("/api/v1/admin/visits", json=body).status_code == 401
    created = demo.post("/api/v1/admin/visits", json=body, headers=admin)
    assert created.status_code == 201 and created.get_json()["replaced_demo"] is True
    series = demo.get(f"{STATS}?granularity=day&place={VELO}", headers=user).get_json()
    row = next(s for s in series["series"] if s["period"] == day)
    assert row["visitors"] == 123 and row["source"] == "registro"
    assert series["quality"]["has_real"] and series["quality"]["has_demo"]
    again = demo.post("/api/v1/admin/visits", json={**body, "visitors": 150}, headers=admin).get_json()
    assert again["replaced_demo"] is False
    recent = demo.get("/api/v1/admin/visits/recent", headers=admin).get_json()["items"]
    assert recent == [{"place": VELO, "place_name": "Catarata Velo de las Ninfas", "date": day, "visitors": 150}]

@pytest.mark.parametrize("payload", [
    {"place": VELO, "date": "2999-01-01", "visitors": 5},
    {"place": VELO, "date": "2026-01-01", "visitors": -1},
    {"place": VELO, "date": "2026-01-01", "visitors": "mucho"},
    {"place": VELO, "date": "ayer", "visitors": 5},
    {"place": "NO VÁLIDO", "date": "2026-01-01", "visitors": 5},
    {"date": "2026-01-01", "visitors": 5},
])
def test_invalid_records_are_rejected(demo, admin, payload):
    assert demo.post("/api/v1/admin/visits", json=payload, headers=admin).status_code in (422,)

def test_unknown_place_cannot_receive_visits(demo, admin):
    r = demo.post("/api/v1/admin/visits", json={"place": "otra", "date": "2026-01-01", "visitors": 5}, headers=admin)
    assert r.status_code == 404

def test_admin_can_remove_the_demo_data_and_real_records_stay(demo, user, admin):
    day = (visits.today_pe() - timedelta(days=2)).isoformat()
    demo.post("/api/v1/admin/visits", json={"place": VELO, "date": day, "visitors": 77}, headers=admin)
    assert demo.delete("/api/v1/admin/visits/demo", headers=user).status_code == 403
    deleted = demo.delete("/api/v1/admin/visits/demo", headers=admin).get_json()["deleted"]
    assert deleted > 4000
    body = demo.get(f"{STATS}?granularity=day", headers=user).get_json()
    assert body["quality"]["has_demo"] is False and body["quality"]["real_days"] == 1
    assert body["forecast"] == [] and body["kpis"]["last_7"] == 77

def test_regional_reference_is_the_real_bcrp_series(demo, user):
    body = demo.get("/api/v1/visits/regional", headers=user).get_json()
    assert body["series_code"] == "RD13362DM" and len(body["monthly"]) == 60 and len(body["yearly"]) == 5
    assert len(body["seasonal_index"]) == 12 and "2019" in body["note"] and body["url"].startswith("https://")


def test_admin_can_remove_a_mistaken_record_but_not_demo_data(app, demo, user, admin):
    day = (visits.today_pe() - timedelta(days=4)).isoformat()
    url = f"/api/v1/admin/visits?place={VELO}&date={day}"
    assert demo.delete(url, headers=admin).status_code == 404                 # ese día solo hay dato de demostración
    demo.post("/api/v1/admin/visits", json={"place": VELO, "date": day, "visitors": 9}, headers=admin)
    assert demo.delete(url, headers=user).status_code == 403
    assert demo.delete(url, headers=admin).get_json() == {"deleted": 1}
    assert demo.get("/api/v1/admin/visits/recent", headers=admin).get_json()["items"] == []
    assert demo.delete(f"/api/v1/admin/visits?place={VELO}&date=ayer", headers=admin).status_code == 422
    assert demo.delete(f"/api/v1/admin/visits?date={day}", headers=admin).status_code == 422
