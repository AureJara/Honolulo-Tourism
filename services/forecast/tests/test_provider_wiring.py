"""El servicio construye el proveedor que indica la configuración, sin conocer ninguna implementación concreta."""

from types import SimpleNamespace

import pytest

from app import create_app
from honolulo_common import weather_provider as wp

BASE = {"TESTING": True, "JWT_SECRET_KEY": "test-secret-test-secret-test-secret-123", "INTERNAL_API_TOKEN": "internal-test"}


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setattr(wp, "_PROVIDERS", dict(wp._PROVIDERS))


def test_the_default_provider_is_open_meteo():
    assert create_app(BASE).extensions["weather_client"].name == "open-meteo"


def test_a_registered_provider_is_used_when_the_config_names_it(registry):
    wp.register_provider("demo")(lambda config: SimpleNamespace(name="demo", last_stats=None))
    app = create_app({**BASE, "WEATHER_PROVIDER": "demo"})
    assert app.extensions["weather_client"].name == "demo"


def test_an_unknown_provider_stops_the_service_at_startup():
    with pytest.raises(RuntimeError, match="WEATHER_PROVIDER desconocido"):
        create_app({**BASE, "WEATHER_PROVIDER": "no-existe"})
