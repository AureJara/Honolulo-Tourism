import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt
import pytest
import responses

from app import create_app
from app.extensions import db
from app.models import WeatherForecast
from honolulo_common.testing import FakeProvider, create_test_database, make_reading

SECRET = "test-secret-test-secret-test-secret-123"
WEATHER_URL = "http://weather.test"
LIMA = ZoneInfo("America/Lima")
UTC = timezone.utc


def now_utc():
    return datetime.now(UTC)


def local_today():
    return now_utc().astimezone(LIMA).date()


def lima_hour(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=LIMA).astimezone(UTC)


@pytest.fixture(scope="session")
def app():
    url = create_test_database("forecast_test")
    app = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": url, "JWT_SECRET_KEY": SECRET,
        "INTERNAL_API_TOKEN": "internal-test", "ENABLE_SCHEDULER": False,
        "WEATHER_SERVICE_URL": WEATHER_URL, "REFRESH_MIN_INTERVAL_S": 60, "EVAL_LEAD_HOURS": 24,
    })
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def provider(app):
    fake = FakeProvider()
    app.extensions["weather_client"] = fake
    with app.app_context():
        names = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
        db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.session.commit()
    return fake


@pytest.fixture()
def client(app, provider):
    return app.test_client()


def make_headers(role="user"):
    now = datetime.now(UTC)
    token = jwt.encode({"sub": "u1", "email": "a@b.co", "name": "Ana", "role": role, "type": "access",
                        "iss": "honolulo-auth", "aud": "honolulo-api", "iat": now,
                        "exp": now + timedelta(minutes=10)}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def headers():
    return make_headers()


@pytest.fixture()
def admin_headers():
    return make_headers("admin")


def add_forecast(target: datetime, issued: datetime, *, temp=26.0, condition="clear", humidity=75,
                 rain_prob=40, precip=0.0, wind=6.0, cloud=40) -> WeatherForecast:
    fc = WeatherForecast(location_code="honolulo", issued_at=issued, target_at=target, temperature_c=temp,
                         feels_like_c=temp + 1, humidity_pct=humidity, precipitation_mm=precip,
                         rain_probability_pct=rain_prob, wind_kph=wind, cloud_cover_pct=cloud, weather_code=0,
                         condition=condition, provider="open-meteo")
    db.session.add(fc)
    return fc


DESIGN_PATTERN = [(22, "fog")] * 4 + [(26, "clear")] * 4 + [(24, "cloudy")] * 2   # horas 8..17


def seed_day(app, day: date, issued: datetime, pattern=DESIGN_PATTERN, **kw) -> list[int]:
    """Guarda un pronóstico para las 10 horas operativas del día (caso del diseño por defecto)."""
    ids = []
    with app.app_context():
        for hour, (temp, cond) in zip(range(8, 18), pattern):
            fc = add_forecast(lima_hour(day, hour), issued, temp=temp, condition=cond, **kw)
            db.session.flush()
            ids.append(fc.id)
        db.session.commit()
    return ids


def mock_observations(observed_fn, *, status=200):
    """Simula GET weather-service/internal/observations. ``observed_fn(hour_utc) -> dict | None``."""
    calls = []

    def callback(request):
        calls.append(request)
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(request.url).query)
        start = datetime.fromisoformat(q["from"][0].replace("Z", "+00:00"))
        end = datetime.fromisoformat(q["to"][0].replace("Z", "+00:00"))
        items, hour, n = [], start, 1
        while hour <= end:
            obs = observed_fn(hour)
            if obs is not None:
                items.append({"id": n, "observed_at": hour.strftime("%Y-%m-%dT%H:%M:%SZ"), **obs})
            hour += timedelta(hours=1)
            n += 1
        return (status, {"Content-Type": "application/json"}, json.dumps({"items": items}))

    responses.add_callback(responses.GET, f"{WEATHER_URL}/internal/observations", callback=callback)
    return calls
