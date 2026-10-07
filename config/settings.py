"""Settings for the attendance tool.

Everything deployment-specific comes from environment variables, or from a
`.env` file next to manage.py. See `.env.example` for the full list.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


_load_dotenv(BASE_DIR / ".env")

# SSLKEYLOGFILE is a debugging switch that makes Python write TLS keys to a
# file. Some Windows builds of Python do not survive it: the whole process
# dies ("no OPENSSL_Applink") the moment a secure connection is prepared,
# which here means the moment the app sends an email. The app has no use for
# key logging, so on Windows it is switched off for this process.
if os.name == "nt":
    os.environ.pop("SSLKEYLOGFILE", None)

DEBUG = env_bool("DEBUG")

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set SECRET_KEY (or DEBUG=true for local development).")
    SECRET_KEY = "insecure-development-key"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS") or ["localhost", "127.0.0.1", "[::1]"]
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Turn on when the app is served over HTTPS behind a reverse proxy.
if env_bool("HTTPS"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "attendance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "attendance.middleware.CloseStaleEntriesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "attendance.context_processors.pending_requests",
                "attendance.context_processors.navigation",
            ],
        },
    },
]

# SQLite by default; set DATABASE_URL=postgres://user:pass@host:5432/name for Postgres.
if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.config(conn_max_age=600, conn_health_checks=True)}
else:
    DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3"}}

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # WAL lets readers and the writer work at the same time; IMMEDIATE makes
    # concurrent writers queue up instead of failing with "database is locked".
    DATABASES["default"]["OPTIONS"] = {
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "attendance.Employee"
AUTHENTICATION_BACKENDS = [
    "attendance.lockout.LockoutBackend",  # locks sign-in after repeated wrong passwords
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
USE_I18N = False
USE_TZ = True
# Timestamps are stored in UTC and shown in this timezone.
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Karachi")

# Hour of the day (0-23) at which one attendance day ends and the next begins.
# Leave at 0 unless people regularly work past midnight.
DAY_ROLLOVER_HOUR = int(os.environ.get("DAY_ROLLOVER_HOUR", "0"))
if not 0 <= DAY_ROLLOVER_HOUR <= 23:
    raise ImproperlyConfigured("DAY_ROLLOVER_HOUR must be between 0 and 23.")

# Email notices go out over SMTP once EMAIL_HOST is set. Without it nothing is
# sent (in development the messages are printed to the console instead).
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
elif DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")  # for port 465
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", not EMAIL_USE_SSL)  # STARTTLS, for port 587
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_FROM") or EMAIL_HOST_USER or "attendance@localhost"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)  # WhiteNoise expects it to exist, even before collectstatic
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
