from __future__ import annotations

from django.apps import AppConfig


class PermkitConfig(AppConfig):
    name = "permkit"
    verbose_name = "permkit"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        pass
