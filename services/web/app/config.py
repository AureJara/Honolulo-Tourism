import os


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SECRET_KEY = os.getenv("WEB_SECRET_KEY", "dev-web-secret-change-me")

    AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:5001")
    WEATHER_SERVICE_URL = os.getenv("WEATHER_SERVICE_URL", "http://127.0.0.1:5002")
    FORECAST_SERVICE_URL = os.getenv("FORECAST_SERVICE_URL", "http://127.0.0.1:5003")
    CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "http://127.0.0.1:5004")
    UPSTREAM_TIMEOUT_S = float(os.getenv("UPSTREAM_TIMEOUT_S", "12"))

    # Cookies de sesión: HttpOnly siempre; Secure en producción (HTTPS).
    COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
    ACCESS_COOKIE = "hn_access"
    REFRESH_COOKIE = "hn_refresh"
    CSRF_COOKIE = "hn_csrf"
    REFRESH_COOKIE_MAX_AGE = int(os.getenv("REFRESH_TOKEN_DAYS", "7")) * 86400
    # Un refresh token ya rotado se reutiliza este tiempo para peticiones paralelas del mismo navegador.
    REFRESH_REUSE_WINDOW_S = int(os.getenv("REFRESH_REUSE_WINDOW_S", "30"))
    # Política de privacidad vigente (debe coincidir con la de auth-service, que registra la aceptación).
    PRIVACY_POLICY_VERSION = os.getenv("PRIVACY_POLICY_VERSION", "2026-10")
    CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "contacto@honolulo-tingomaria.pe")

    # Sesión firmada de Flask: solo guarda el correo pendiente de confirmar y mensajes (flash).
    SESSION_COOKIE_NAME = "hn_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

    # Las peticiones normales son diminutas; solo la subida de fotos del administrador es grande.
    SMALL_REQUEST_LIMIT = 32 * 1024
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    MAX_PHOTO_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
