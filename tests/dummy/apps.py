from __future__ import annotations

from django.apps import AppConfig


class DummyConfig(AppConfig):
    name = "tests.dummy"
    label = "dummy"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Every module carrying a declaration must be imported for the
        # registry to be complete.  Views would otherwise only load when the
        # URLconf is first resolved — too late for a catalogue sync, which
        # runs without serving a request.
        from . import filters, serializers, views  # noqa: F401
