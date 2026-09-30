import os


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/forecast_db")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me-dev-secret-change-me")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "honolulo-auth")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "honolulo-api")
    INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "dev-internal-token")

    PROVIDER_BASE_URL = os.getenv("WEATHER_PROVIDER_URL", "https://api.open-meteo.com/v1/forecast")
    PROVIDER_TIMEOUT_S = float(os.getenv("WEATHER_PROVIDER_TIMEOUT_S", "15"))
    WEATHER_SERVICE_URL = os.getenv("WEATHER_SERVICE_URL", "http://127.0.0.1:5002")
    WEATHER_SERVICE_TIMEOUT_S = float(os.getenv("WEATHER_SERVICE_TIMEOUT_S", "10"))

    FORECAST_DAYS = int(os.getenv("FORECAST_DAYS", "16"))
    FORECAST_REFRESH_HOURS = int(os.getenv("FORECAST_REFRESH_HOURS", "3"))
    # Intervalo mínimo entre consultas al proveedor disparadas por usuarios (RF07).
    REFRESH_MIN_INTERVAL_S = int(os.getenv("REFRESH_MIN_INTERVAL_S", "60"))
    # Para evaluar la hora H se usa el último pronóstico emitido al menos N horas antes (sin «mirar atrás»).
    EVAL_LEAD_HOURS = int(os.getenv("EVAL_LEAD_HOURS", "24"))
    EVAL_LOOKBACK_HOURS = int(os.getenv("EVAL_LOOKBACK_HOURS", "72"))
    # Margen para que el proveedor consolide la hora antes de compararla.
    EVAL_GRACE_HOURS = int(os.getenv("EVAL_GRACE_HOURS", "1"))
    FORECAST_RETENTION_DAYS = int(os.getenv("FORECAST_RETENTION_DAYS", "548"))

    ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"
