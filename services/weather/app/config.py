import os


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/weather_db")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "honolulo-auth")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "honolulo-api")
    INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

    # Proveedor meteorológico: se elige por nombre (ver honolulo_common/weather_provider.py).
    WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open-meteo")
    WEATHER_PROVIDER_MODULES = os.getenv("WEATHER_PROVIDER_MODULES", "")      # módulos extra que registran proveedores
    PROVIDER_BASE_URL = os.getenv("WEATHER_PROVIDER_URL", "https://api.open-meteo.com/v1/forecast")
    PROVIDER_TIMEOUT_S = float(os.getenv("WEATHER_PROVIDER_TIMEOUT_S", "10"))

    # El dato actual se considera vigente hasta este límite; después se intenta refrescar (RN08).
    CURRENT_MAX_AGE_MIN = int(os.getenv("CURRENT_MAX_AGE_MIN", "30"))
    # Intervalo mínimo entre consultas al proveedor disparadas por usuarios (RF07): protege la cuota.
    REFRESH_MIN_INTERVAL_S = int(os.getenv("REFRESH_MIN_INTERVAL_S", "60"))
    REFRESH_CURRENT_MINUTES = int(os.getenv("REFRESH_CURRENT_MINUTES", "15"))
    HOURLY_SYNC_DAYS = int(os.getenv("HOURLY_SYNC_DAYS", "3"))
    # Mínimo entre sincronizaciones horarias disparadas por pedidos de otros servicios.
    HOURLY_SYNC_MIN_INTERVAL_S = int(os.getenv("HOURLY_SYNC_MIN_INTERVAL_S", "600"))

    ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"
