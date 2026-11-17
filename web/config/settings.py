"""Django settings. Every secret and deployment choice comes from the environment.

Required in production:
  DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, FIELD_ENCRYPTION_KEYS (comma-separated
  Fernet keys, newest first), BLIND_INDEX_KEY (32+ characters), PGHOST, PGDATABASE,
  PGUSER, PGPASSWORD.
Optional: DJANGO_DEBUG (default 0), DJANGO_SECURE_SSL_REDIRECT (default 1),
  DJANGO_CSRF_TRUSTED_ORIGINS (e.g. https://compliance.sacco.co.ke), RISK_SETTINGS_VERSION,
  PGPORT, and the job, import and email settings further down. See docs/DEPLOYMENT.md.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required")
    return value or ""


def env_bool(name: str, default: bool) -> bool:
    return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "audittrail",
    "members",
    "screening",
    "cases",
    "corebanking",
    "monitoring",
    "jobs",
    "assurance",
    "restrictions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",          # serves static files from the app itself
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.LoginAndMfaRequired",
    "accounts.middleware.DatabaseRole",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "members.context.institution",
    ]},
}]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("PGHOST", "localhost"),
        "PORT": env("PGPORT", "5432"),
        "NAME": env("PGDATABASE", "compliance_desk"),
        "USER": env("PGUSER", "compliance_app"),
        "PASSWORD": env("PGPASSWORD", ""),
        "CONN_MAX_AGE": 0,          # the per-request role setting must not leak between requests
        "ATOMIC_REQUESTS": False,
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Sessions: 30 minutes of inactivity, ended when the browser closes.
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
# On-screen messages can name cases; keep them server-side, tied to the session
# (cookie storage is signed but readable, and survives a change of user).
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# HSTS preload commits the whole domain to HTTPS in browsers' built-in lists; decide per SACCO.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Login lockout: 5 failures locks the username for 15 minutes.
# Use a shared cache (e.g. Redis) when running more than one app process.
# The login lockout counter is kept in the database cache so every app process
# (gunicorn worker) shares it. `manage.py createcachetable` creates the table.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG and env_bool("STATIC_MANIFEST", True)
                    else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Field encryption (see compliance_core.crypto). Keys never live in the code or database.
FIELD_ENCRYPTION_KEYS = [k.strip() for k in env("FIELD_ENCRYPTION_KEYS", required=True).split(",") if k.strip()]
BLIND_INDEX_KEY = env("BLIND_INDEX_KEY", required=True)

# The board-approved risk settings in force. Until a version is approved, assessments
# are labelled with this placeholder so they can be found and re-run later.
# Uploaded list files larger than this are refused.
LIST_UPLOAD_MAX_BYTES = 50 * 1024 * 1024

RISK_SETTINGS_VERSION = env("RISK_SETTINGS_VERSION", "default-not-yet-approved")
INSTITUTION_NAME = env("INSTITUTION_NAME", "Demo SACCO")

# KYC documents: encrypted files kept outside the database. Back this folder up with
# the database (deploy/backup.sh does both) and keep the encryption key separately.
DOCUMENT_ROOT = env("DOCUMENT_ROOT", str(BASE_DIR / "documents"))
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024

# Core banking imports. IMPORT_INBOX is a folder the core banking system (or an SFTP
# drop) writes CSV files to; the nightly job imports them. Leave empty to import by hand.
IMPORT_MAX_BYTES = 100 * 1024 * 1024
IMPORT_INBOX = env("IMPORT_INBOX", "")

# Telling core banking about holds and freezes. Either or both:
#  * a web link the core banking system (or its integration layer) exposes; each change
#    is POSTed as JSON signed with HMAC-SHA256 in the X-Compliance-Desk-Signature header;
#  * an outbox folder: the full list of restricted members is written there as CSV.
CORE_BANKING_WEBHOOK_URL = env("CORE_BANKING_WEBHOOK_URL", "")
CORE_BANKING_WEBHOOK_SECRET = env("CORE_BANKING_WEBHOOK_SECRET", "")
EXPORT_OUTBOX = env("EXPORT_OUTBOX", "")
if CORE_BANKING_WEBHOOK_URL and len(CORE_BANKING_WEBHOOK_SECRET) < 32:
    raise ImproperlyConfigured("CORE_BANKING_WEBHOOK_SECRET must be at least 32 characters when a web link is set")

# Transaction monitoring. KES per US dollar for the USD 15,000 large cash threshold
# (reg. 35). The administrator should keep this in line with the CBK rate.
USD_KES_RATE = env("USD_KES_RATE", "129.00")

# Daily sanctions list download. Check the address against the UN Security Council
# website before go-live; it can be pointed at a local mirror.
UN_LIST_URL = env("UN_LIST_URL", "https://scsanctions.un.org/resources/xml/en/consolidated.xml")
UN_LIST_NAME = "UN Security Council consolidated list"

# Reminder emails. The default prints emails to the log; set EMAIL_BACKEND to
# django.core.mail.backends.smtp.EmailBackend and the EMAIL_* settings to send them.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "compliance-desk@localhost")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
