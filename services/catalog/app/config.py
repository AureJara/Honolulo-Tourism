import os


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/catalog_db")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me-dev-secret-change-me")
    JWT_ISSUER = os.getenv("JWT_ISSUER", "honolulo-auth")
    JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "honolulo-api")

    # Almacenamiento local de fotos (volumen en Docker). La interfaz de storage.py permite cambiar a S3.
    MEDIA_ROOT = os.getenv("MEDIA_ROOT", ".media")
    MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    MAX_PHOTOS_PER_PLACE = int(os.getenv("MAX_PHOTOS_PER_PLACE", "12"))
    # Tope de la petición completa (archivo + campos del formulario).
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))) + 64 * 1024
