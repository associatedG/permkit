SECRET_KEY = "permkit-tests"
DEBUG = False
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "permkit",
    "tests.dummy",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

AUTH_USER_MODEL = "dummy.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
