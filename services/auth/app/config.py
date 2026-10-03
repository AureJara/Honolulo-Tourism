import os

_ENV = os.getenv("APP_ENV", "development")


class Config:
    APP_ENV = _ENV
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/auth_db")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # HS256 con secreto compartido entre servicios (ver README: evolución a RS256/JWKS).
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "honolulo-auth")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "honolulo-api")

    ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
    REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))
    MAX_FAILED_ATTEMPTS = int(os.getenv("MAX_FAILED_ATTEMPTS", "5"))
    LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
    PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))
    PASSWORD_HASH_METHOD = os.getenv("PASSWORD_HASH_METHOD", "scrypt")   # las pruebas usan uno más barato
    MAX_CONTENT_LENGTH = 16 * 1024

    # Política de privacidad vigente que el usuario debe aceptar al registrarse (Escenario 7).
    PRIVACY_POLICY_VERSION = os.getenv("PRIVACY_POLICY_VERSION", "2026-10-03")

    # Confirmación del correo con código de 6 dígitos (Escenario 7).
    VERIFICATION_CODE_TTL_MIN = int(os.getenv("VERIFICATION_CODE_TTL_MIN", "10"))
    VERIFICATION_MAX_ATTEMPTS = int(os.getenv("VERIFICATION_MAX_ATTEMPTS", "5"))
    VERIFICATION_RESEND_SECONDS = int(os.getenv("VERIFICATION_RESEND_SECONDS", "60"))
    VERIFICATION_MAX_PER_HOUR = int(os.getenv("VERIFICATION_MAX_PER_HOUR", "5"))

    # Correo saliente: smtp (producción) | file (desarrollo y QA) | memory (pruebas)
    MAIL_BACKEND = os.getenv("MAIL_BACKEND", "smtp" if _ENV == "production" else "file")
    MAIL_FROM = os.getenv("MAIL_FROM", "Honolulo <no-reply@honolulo.local>")
    MAIL_OUTBOX_DIR = os.getenv("MAIL_OUTBOX_DIR", ".mail")
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls")          # starttls | ssl | none
    SMTP_TIMEOUT_S = float(os.getenv("SMTP_TIMEOUT_S", "10"))
