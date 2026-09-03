"""Settings plumbing and the process-wide default Policy."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

DEFAULTS: dict[str, Any] = {
    "PRINCIPAL_RESOLVER": "permkit.principals.AttributeRoleResolver",
    "PRINCIPAL_RESOLVER_KWARGS": {"attribute": "role"},
    "STORE": "permkit.store.DatabaseStore",
    "STORE_KWARGS": {},
    # Superuser bypass is a deliberate, visible switch rather than an
    # accident of some `if user.is_superuser` scattered through the code.
    "SUPERUSER_BYPASS": True,
    "CONTEXT_BUILDER": None,
    # Resolve a user's roles once per user object rather than once per check.
    # Free for the default resolver (an attribute read); the difference is a
    # resolver that reads the database, where it turns a query per check into
    # one per request.
    "CACHE_ROLES": True,
    # Which per-app modules ``permkit_sync`` imports before scraping the
    # registry.  None means the conventional list in
    # ``permkit.catalogue.loading``; set it only if declarations live under
    # names that list does not cover.
    "DECLARATION_MODULES": None,
}


def get_setting(name: str) -> Any:
    return getattr(settings, "PERMKIT", {}).get(name, DEFAULTS[name])


_policy = None


def get_policy():
    """Build (once) the Policy described by settings."""
    global _policy
    if _policy is None:
        from .resolver import Policy

        principals = import_string(get_setting("PRINCIPAL_RESOLVER"))(
            **get_setting("PRINCIPAL_RESOLVER_KWARGS")
        )
        store = import_string(get_setting("STORE"))(**get_setting("STORE_KWARGS"))
        builder = get_setting("CONTEXT_BUILDER")
        _policy = Policy(
            store=store,
            principals=principals,
            superuser_bypass=get_setting("SUPERUSER_BYPASS"),
            cache_roles=get_setting("CACHE_ROLES"),
            context_builder=import_string(builder) if builder else None,
        )
    return _policy


def set_policy(policy) -> None:
    """Install a Policy explicitly. Test helper."""
    global _policy
    _policy = policy


def reset_policy() -> None:
    global _policy
    _policy = None
