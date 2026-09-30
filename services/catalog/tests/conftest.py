import io
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.seed import seed_places
from honolulo_common.testing import create_test_database

SECRET = "test-secret-test-secret-test-secret-123"


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    url = create_test_database("catalog_test")
    app = create_app({
        "TESTING": True, "SQLALCHEMY_DATABASE_URI": url, "JWT_SECRET_KEY": SECRET,
        "MEDIA_ROOT": str(tmp_path_factory.mktemp("media")),
        "MAX_UPLOAD_BYTES": 300 * 1024, "MAX_CONTENT_LENGTH": 400 * 1024, "MAX_PHOTOS_PER_PLACE": 4,
    })
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client(app):
    with app.app_context():
        names = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
        db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.session.commit()
    for f in app.extensions["storage"].root.glob("*"):
        f.unlink()
    return app.test_client()


@pytest.fixture()
def seeded(app, client):
    with app.app_context():
        assert seed_places() == 4
    return client


def token(role="user", name="Ana Ríos", sub="u-1", **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": sub, "email": "a@b.co", "name": name, "role": role, "type": "access",
              "iss": "honolulo-auth", "aud": "honolulo-api", "iat": now, "exp": now + timedelta(minutes=10)}
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture()
def admin():
    return {"Authorization": f"Bearer {token('admin', name='Admin Honolulo', sub='admin-1')}"}


@pytest.fixture()
def user():
    return {"Authorization": f"Bearer {token('user')}"}


def make_image(fmt="JPEG", size=(800, 600), color=(40, 140, 90), **save_kwargs) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt, **save_kwargs)
    return out.getvalue()


def upload(client, headers, place_id, data, alt="Cascada entre la selva", filename="foto.jpg", **form):
    return client.post(f"/api/v1/admin/places/{place_id}/photos", headers=headers,
                       data={"file": (io.BytesIO(data), filename), "alt_text": alt, **form},
                       content_type="multipart/form-data")


def first_place_id(client, headers):
    return client.get("/api/v1/admin/places", headers=headers).get_json()["items"][0]["id"]
