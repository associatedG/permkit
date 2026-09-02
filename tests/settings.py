"""Settings for the test suite and for the runnable dummy demo.

The same module serves both, so what the demo exercises is what the tests
exercise. ``PERMKIT_DB`` is the only difference: unset it and the suite runs
in memory; point it at a file and ``manage.py runserver`` has somewhere to
keep the permissions you compose.
"""

import os

SECRET_KEY = "permkit-tests"
DEBUG = True
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # The admin stack, so the dummy domain has a real UI to configure.
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "permkit",
    "tests.dummy",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("PERMKIT_DB", ":memory:"),
    }
}

AUTH_USER_MODEL = "dummy.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"

ROOT_URLCONF = "tests.dummy.urls"

REST_FRAMEWORK = {
    # The library's whole premise: a view that declares nothing is closed.
    "DEFAULT_PERMISSION_CLASSES": ["permkit.drf.DenyAll"],
    # Without this, a Layer 1 denial surfaces as a 500 rather than a 403.
    "EXCEPTION_HANDLER": "permkit.drf.exception_handler",
}

PERMKIT = {
    "PRINCIPAL_RESOLVER_KWARGS": {"attribute": "role"},
}
