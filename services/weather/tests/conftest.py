from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import create_app
from app.extensions import db
from honolulo_common.testing import FakeProvider, create_test_database, make_reading

SECRET = "test-secret-test-secret-test-secret-123"

__all__ = ["make_reading", "hour_ago"]


def hour_ago(n):
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=n)


@pytest.fixture(scope="session")
def app():
    url = create_test_database("weather_test")
    app = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": url, "JWT_SECRET_KEY": SECRET,
        "INTERNAL_API_TOKEN": "internal-test", "ENABLE_SCHEDULER": False,
        "REFRESH_MIN_INTERVAL_S": 60, "CURRENT_MAX_AGE_MIN": 30, "HOURLY_SYNC_MIN_INTERVAL_S": 600,
    })
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def provider(app):
    fake = FakeProvider()
    fake.current = make_reading()
    app.extensions["weather_client"] = fake
    with app.app_context():
        names = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
        db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.session.commit()
    return fake


@pytest.fixture()
def client(app, provider):
    return app.test_client()


@pytest.fixture()
def headers(app):
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "u1", "email": "a@b.co", "name": "Ana", "role": "user", "type": "access",
                        "iss": "honolulo-auth", "aud": "honolulo-api", "iat": now,
                        "exp": now + timedelta(minutes=10)}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def internal():
    return {"X-Internal-Token": "internal-test"}
