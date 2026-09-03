"""Force every declaration into the registry before the catalogue is scraped.

Declarations register as a side effect of importing the module they live in.
That is fine at request time — resolving the URLconf imports the views, which
import the serializers, selectors and services underneath them — and wrong at
sync time, because ``permkit_sync`` never serves a request.  A sync run on a
half-imported registry publishes an incomplete catalogue and then marks the
missing declarations dead on the next run, which is worse than not syncing:
the admin sees a live filter turn stale for no reason anybody can explain.

So the sync forces the imports itself, from two directions:

* **the URLconf**, walked to the leaves, which is what pulls in the HTTP-facing
  declarations the way a first request would;
* **conventional module names** in every installed app, which is what catches
  the ones no URL reaches — a service called only from a Celery task, a
  selector used only by a management command.

Neither is sufficient alone, and both are cheap and idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module

from django.apps import apps
from django.urls import URLResolver, get_resolver
from django.utils.module_loading import module_has_submodule

#: Where declarations conventionally live.  Override with
#: ``PERMKIT = {"DECLARATION_MODULES": [...]}`` for a project that names them
#: differently; the cost of a wrong list is a silently thin catalogue, so the
#: default is deliberately generous.
DEFAULT_DECLARATION_MODULES = (
    "filters",
    "selectors",
    "serializers",
    "services",
    "views",
    "permissions",
    "apis",
    "tasks",
)


@dataclass
class LoadReport:
    modules: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    urlconf_resolved: bool = False

    def __str__(self) -> str:
        where = f"{len(self.modules)} module(s) across {len(self.apps)} app(s)"
        return where + (" + URLconf" if self.urlconf_resolved else "")


def _force_urlconf(report: LoadReport) -> None:
    """Walk the URL tree to its leaves.

    ``include()`` is lazy: the included module is imported only when its
    ``url_patterns`` are first touched.  Touching them here is what makes the
    walk equivalent to having served one request against every route.
    """

    def walk(resolver: URLResolver) -> None:
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern)

    walk(get_resolver())
    report.urlconf_resolved = True


def _force_app_modules(report: LoadReport, names: tuple[str, ...]) -> None:
    for config in apps.get_app_configs():
        touched = False
        for name in names:
            # ``module_has_submodule`` distinguishes "this app has no
            # services.py" from "services.py raised ImportError".  The first is
            # ordinary; the second must surface, not be swallowed into a
            # catalogue that is quietly missing an endpoint.
            if not module_has_submodule(config.module, name):
                continue
            import_module(f"{config.name}.{name}")
            report.modules.append(f"{config.name}.{name}")
            touched = True
        if touched:
            report.apps.append(config.label)


def load_declarations(*, urlconf: bool = True) -> LoadReport:
    """Import everything that might register, and say what was imported."""
    from ..conf import get_setting

    report = LoadReport()
    names = tuple(get_setting("DECLARATION_MODULES") or DEFAULT_DECLARATION_MODULES)
    _force_app_modules(report, names)
    if urlconf:
        _force_urlconf(report)
    return report
