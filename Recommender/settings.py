# settings.py
from pathlib import Path
from datetime import timedelta
import os
from dotenv import load_dotenv
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
# ---------------------- PATHS & ENV ----------------------
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv()

# ---------------------- API KEY --------------------------
LAST_FM_API_KEY=os.getenv("LAST_FM_API_KEY")

# ---------------------- SECURITY / DEBUG ----------------------
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-change-me")
DEBUG = os.getenv("DEBUG", "True") == "True"
ALLOWED_HOSTS = ["127.0.0.1", "localhost",'host.docker.internal']
INTERNAL_IPS = ["127.0.0.1", "localhost"]
# ---------------------- CORS / CSRF ----------------------
# Frontend: 127.0.0.1:5173 – keep host consistent
CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:5173"]
# CORS_ALLOW_CREDENTIALS defaults to False — no need to set explicitly

# ---------------------- DJANGO CORE ----------------------
INSTALLED_APPS = [
    # Django
    "django_prometheus",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",

    # Third-party
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "drf_spectacular_sidecar",  # Uncomment only if you want local Swagger UI assets
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.spotify",
    "djoser",
    "debug_toolbar",
    'django_celery_beat',
    'django_celery_results',

    # Local apps
    'users.apps.UsersConfig',
    'recomendations',
]

SITE_ID = 1

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "Recommender.middleware.JWTAuthCookieMiddleware",
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = 'Recommender.urls'

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Remove 'DIRS' if API-only and not using custom templates
        # 'DIRS': [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = 'Recommender.wsgi.application'


# ---------------------- DATABASE ----------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ---------------------- PASSWORD VALIDATORS ----------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------- INTERNATIONALIZATION ----------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ---------------------- STATIC FILES ----------------------
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------- USERS / AUTH ----------------------
AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]


# ---------------------- DRF / REST ----------------------
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],

    # SimpleJWT (Authorization: Bearer <token>)
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),

    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

DJOSER = {
    "LOGIN_FIELD": "email",
    "SERIALIZERS": {
        "user_create": "users.serializers.CustomRegisterSerializer",
        "user": "users.serializers.CustomRegisterSerializer",
    },
}


# ---------------------- SIMPLE JWT ----------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}


# ---------------------- ALLAUTH / SOCIAL ----------------------
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_VERIFICATION = "optional"  # change to 'mandatory' in production
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_LOGOUT_ON_GET = True
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    "spotify": {
        "APP": {
            "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
            "secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
        },
        "SCOPE": [
            "user-top-read",
            "user-read-recently-played",
            "playlist-read-private",
            "playlist-read-collaborative",
            "user-library-read",
            "user-read-private",
            "user-read-email",
        ],
        "AUTH_PARAMS": {"show_dialog": False},
    }
}

LOGIN_URL = "auth/jwt/create"
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"


# ---------------------- SPECTACULAR / SWAGGER ----------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "Music Recommender",
    "DESCRIPTION": "API for user management and Spotify integration",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}




# ---------------------- EMAIL (DEV) ----------------------
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

SENTRY_DSN = os.getenv("SENTRY_DSN")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.2,      # v1: 20% requests
        send_default_pii=False,      # GDPR-safe
        environment=os.getenv("ENV", "local"),
    )