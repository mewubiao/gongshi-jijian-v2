"""项目配置。本地默认值可以直接跑；部署时改环境变量。"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 这个默认值不是密钥，只为了本机不用先配环境变量。
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-dev-only")

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "unfold.apps.BasicAppConfig",
    "accounts.apps.BusinessAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "masterdata",
    "erp",
    "dispatch",
    "reporting",
    "timekeeping",
    "settlement",
    "terminal",
]

UNFOLD = {
    "SITE_TITLE": "工时计件",
    "SITE_HEADER": "工时计件",
    "SITE_SUBHEADER": "生产管理后台",
    "SITE_SYMBOL": "timer",
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "#eff6ff",
            "100": "#dbeafe",
            "200": "#bfdbfe",
            "300": "#93c5fd",
            "400": "#60a5fa",
            "500": "#3b82f6",
            "600": "#2563eb",
            "700": "#1d4ed8",
            "800": "#1e40af",
            "900": "#1e3a8a",
            "950": "#172554",
        },
    },
    "SIDEBAR": {
        "navigation": "accounts.navigation.admin_navigation",
    },
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.RevokeInvalidSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

# 易飞 SQL Server 不放进 DATABASES。同步用 pyodbc 只读连接，配置见 docs/ERP_SYNC.md。

_VALIDATOR = "django.contrib.auth.password_validation"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"{_VALIDATOR}.UserAttributeSimilarityValidator"},
    {"NAME": f"{_VALIDATOR}.MinimumLengthValidator"},
    {"NAME": f"{_VALIDATOR}.CommonPasswordValidator"},
    {"NAME": f"{_VALIDATOR}.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"

TIME_ZONE = "Asia/Shanghai"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = ["accounts.backends.EmployeeBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
