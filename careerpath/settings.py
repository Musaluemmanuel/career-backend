import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-careerpath-development-key")
AUTH_TOKEN_SECRET = os.environ.get("AUTH_TOKEN_SECRET") or os.environ.get("SECRET_KEY", "")
AUTH_TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_TOKEN_TTL_SECONDS", "604800"))
DEBUG = os.environ.get("DJANGO_DEBUG", "").lower() == "true"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "api.middleware.CorsMiddleware",
]

ROOT_URLCONF = "careerpath.urls"
WSGI_APPLICATION = "careerpath.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


def _database_settings():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "local_test.db",
            "CONN_MAX_AGE": 60,
        }

    parsed = urlparse(database_url)
    if parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        query_options = parse_qs(parsed.query)
        sslmode = query_options.get("sslmode", [None])[0]
        if not sslmode and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            sslmode = "require"
        database_options = {}
        if sslmode:
            database_options["sslmode"] = sslmode
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
            "CONN_MAX_AGE": 60,
            "OPTIONS": database_options,
        }
    if parsed.scheme == "sqlite":
        name = unquote(parsed.path)
        if name.startswith("//"):
            name = name[1:]
        elif name.startswith("/"):
            name = name[1:]
        if name and not os.path.isabs(name):
            name = str(BASE_DIR / name)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name or BASE_DIR / "local_test.db",
            "CONN_MAX_AGE": 60,
        }
    raise ValueError("DATABASE_URL must use PostgreSQL or SQLite.")


DATABASES = {"default": _database_settings()}

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
EMAIL_HOST_USER = os.environ.get("MAIL_USERNAME", "")
EMAIL_HOST_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or "CareerPath AI <no-reply@careerpath.local>"
EMAIL_TIMEOUT = 15

CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "")
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "108086559679-et4vvki3fehs0beefbv8bn9psonh5ubp.apps.googleusercontent.com",
)
PORT = int(os.environ.get("PORT", "5001"))
