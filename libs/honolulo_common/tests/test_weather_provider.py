"""Los servicios dependen de la interfaz ``WeatherProvider``; el proveedor concreto se elige por configuración."""

from datetime import datetime, timezone

import pytest

from honolulo_common import openmeteo, weather_provider as wp
from honolulo_common.testing import FakeProvider, make_reading
from honolulo_common.weather_provider import (ProviderError, Reading, WeatherProvider, create_provider,
                                              register_provider)

CONFIG = {"PROVIDER_BASE_URL": "http://proveedor.test/v1", "PROVIDER_TIMEOUT_S": 3.0}


@pytest.fixture()
def registry(monkeypatch):
    monkeypatch.setattr(wp, "_PROVIDERS", dict(wp._PROVIDERS))


def test_open_meteo_is_the_default_provider_and_honours_the_config():
    provider = create_provider(CONFIG)
    assert isinstance(provider, openmeteo.OpenMeteoClient) and provider.name == "open-meteo"
    assert provider.base_url == "http://proveedor.test/v1" and provider.timeout == 3.0


def test_providers_satisfy_the_interface():
    assert isinstance(create_provider(CONFIG), WeatherProvider)
    assert isinstance(FakeProvider(), WeatherProvider)


def test_a_new_provider_is_chosen_by_name_without_touching_any_service(registry):
    class Fixed:
        name = "estacion-local"
        last_stats = None

        def __init__(self, config):
            self.config = config

        def fetch_current(self, lat, lon, tz):
            return Reading(datetime(2026, 10, 3, 12, tzinfo=timezone.utc), 25.0, 26.0, 70, 0.0, 10, 5.0, 20, 1, True)

        def fetch_hourly(self, lat, lon, tz, *, forecast_days=16, past_days=0):
            return []

    register_provider("estacion-local")(Fixed)
    provider = create_provider({**CONFIG, "WEATHER_PROVIDER": "estacion-local"})
    assert provider.name == "estacion-local" and provider.fetch_current(0, 0, "UTC").temperature_c == 25.0
    assert provider.config["PROVIDER_TIMEOUT_S"] == 3.0                              # recibe la configuración del servicio


def test_an_unknown_provider_is_a_clear_error_listing_the_available_ones():
    with pytest.raises(RuntimeError, match="no-existe.*open-meteo"):
        create_provider({**CONFIG, "WEATHER_PROVIDER": "no-existe"})


def test_extra_modules_can_register_providers(registry, tmp_path, monkeypatch):
    module = tmp_path / "proveedor_externo.py"
    module.write_text(
        "from honolulo_common.weather_provider import register_provider\n"
        "class P:\n    name = 'externo'\n    last_stats = None\n"
        "    def fetch_current(self, *a, **k): return None\n    def fetch_hourly(self, *a, **k): return []\n"
        "register_provider('externo')(lambda config: P())\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    provider = create_provider({**CONFIG, "WEATHER_PROVIDER": "externo", "WEATHER_PROVIDER_MODULES": "proveedor_externo"})
    assert provider.name == "externo"


def test_provider_errors_and_readings_are_provider_agnostic_types():
    assert openmeteo.ProviderError is ProviderError and openmeteo.Reading is Reading
    err = ProviderError("caído", http_status=503)
    assert err.http_status == 503 and str(err) == "caído"
    assert make_reading().temperature_c == 26.4
