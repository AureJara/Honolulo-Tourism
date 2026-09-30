from datetime import timedelta

import pytest

from app import service
from app.extensions import db
from app.models import ProviderFetchLog, WeatherForecast
from conftest import add_forecast, headers, now_utc  # noqa: F401
from honolulo_common.testing import make_reading

API = "/api/v1/weather"


def floor_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def count_forecasts(app):
    with app.app_context():
        return db.session.execute(db.select(db.func.count()).select_from(WeatherForecast)).scalar_one()


# ------------------------------------------------------------------ ingesta
def test_refresh_stores_only_future_hours_with_issue_and_target(app, provider):
    base = floor_hour(now_utc())
    provider.hourly = [make_reading(when=base + timedelta(hours=h), temp=20 + h) for h in range(-2, 6)]
    with app.app_context():
        assert service.refresh_forecast() == 5                      # solo h = 1..5 (RN05: antes de la hora objetivo)
        rows = db.session.execute(db.select(WeatherForecast).order_by(WeatherForecast.target_at)).scalars().all()
    assert len(rows) == 5
    assert all(r.target_at > r.issued_at for r in rows)
    assert all(r.location_code == "honolulo" and r.provider == "open-meteo" for r in rows)
    assert rows[0].condition == "partly_cloudy" and rows[0].rain_probability_pct == 35


def test_forecasts_are_append_only_and_refresh_is_idempotent_within_a_minute(app, provider):
    base = floor_hour(now_utc())
    future = base + timedelta(hours=10)
    with app.app_context():
        add_forecast(future, now_utc() - timedelta(hours=3), temp=20)          # emisión anterior
        db.session.commit()
    provider.hourly = [make_reading(when=future, temp=22)]
    with app.app_context():
        service.refresh_forecast()
        service.refresh_forecast()                                              # mismo minuto: no duplica
    assert count_forecasts(app) == 2                                            # la anterior se conserva


def test_provider_failure_raises_and_is_logged_without_inventing_data(app, provider):
    provider.fail = True
    from honolulo_common.openmeteo import ProviderError
    with app.app_context(), pytest.raises(ProviderError):
        service.refresh_forecast()
    with app.app_context():
        log = db.session.execute(db.select(ProviderFetchLog)).scalars().all()
    assert len(log) == 1 and log[0].ok is False
    assert count_forecasts(app) == 0


def test_manual_refresh_endpoint_debounces_and_reports_failures(app, client, provider, headers):
    base = floor_hour(now_utc())
    provider.hourly = [make_reading(when=base + timedelta(hours=3))]
    first = client.post(f"{API}/forecast/refresh", headers=headers).get_json()
    assert first["refreshed"] is True and first["rows"] == 1
    second = client.post(f"{API}/forecast/refresh", headers=headers).get_json()
    assert second["refreshed"] is False and second["reason"] == "recent"
    assert provider.calls == [("hourly", 0)]


def test_manual_refresh_failure_is_502_with_spec_message(app, client, provider, headers):
    provider.fail = True
    resp = client.post(f"{API}/forecast/refresh", headers=headers)
    assert resp.status_code == 502
    assert resp.get_json()["detail"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
    calls = len(provider.calls)
    assert client.post(f"{API}/forecast/refresh", headers=headers).status_code == 502
    assert len(provider.calls) == calls                                         # no insiste contra el proveedor


# -------------------------------------------------- pronóstico de referencia
def test_future_hours_use_latest_issued_forecast(app, provider):
    target = floor_hour(now_utc()) + timedelta(hours=30)
    with app.app_context():
        add_forecast(target, now_utc() - timedelta(hours=10), temp=20)
        add_forecast(target, now_utc() - timedelta(hours=1), temp=22)
        db.session.commit()
        ref = service.reference_forecasts(target, target + timedelta(hours=1))
    assert ref[target].temperature_c == 22


def test_past_hours_use_latest_forecast_issued_with_enough_lead_and_never_hindsight(app, provider):
    target = floor_hour(now_utc()) - timedelta(hours=30)
    with app.app_context():
        add_forecast(target, target - timedelta(hours=48), temp=18)            # antelación 48 h
        add_forecast(target, target - timedelta(hours=26), temp=19)            # antelación 26 h  <- referencia
        add_forecast(target, target - timedelta(hours=3), temp=21)             # antelación 3 h (< 24 h)
        add_forecast(target, target + timedelta(hours=1), temp=30)             # emitido DESPUÉS de la hora
        db.session.commit()
        ref = service.reference_forecasts(target, target + timedelta(hours=1))
    assert ref[target].temperature_c == 19


def test_past_hours_fall_back_to_latest_issued_before_target(app, provider):
    target = floor_hour(now_utc()) - timedelta(hours=5)
    with app.app_context():
        add_forecast(target, target - timedelta(hours=6), temp=20)
        add_forecast(target, target - timedelta(hours=2), temp=21)
        add_forecast(target, target + timedelta(minutes=30), temp=35)           # posterior: se ignora
        db.session.commit()
        ref = service.reference_forecasts(target, target + timedelta(hours=1))
    assert ref[target].temperature_c == 21


def test_hours_without_any_forecast_are_absent(app, provider):
    target = floor_hour(now_utc()) + timedelta(hours=3)
    with app.app_context():
        assert service.reference_forecasts(target, target + timedelta(hours=1)) == {}
