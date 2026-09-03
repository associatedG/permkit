"""permkit — role-based authorization for Django across three tiers.

    endpoint  may this role attempt this at all
    object    which rows, compiled into the SQL WHERE clause
    field     which fields, read and write configured separately

Read and write never share a grant: they are different keys.

Layer 1 is the five functions below — no request, no view, no serializer, so
they work equally in selectors, services, tasks and commands.  Layer 2
(``permkit.drf``, ``permkit.decorators``) is thin ergonomics over them.

The imports here are deliberately lazy: this package is listed in
INSTALLED_APPS, so it must not pull in models at import time.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import Context, ObjectCondition, Param, Tier
from .exceptions import (
    ConfigurationError,
    InvalidParams,
    PermissionDenied,
    PermKitError,
    UnknownCondition,
    UnknownKey,
)
from .declare import (
    api_permission,
    apply_permissions,
    field_groups,
    condition_params,
    object_condition,
    object_permissions,
    permission_object,
)
from .registry import register_condition, register_key, registry

default_app_config = "permkit.apps.PermkitConfig"

__all__ = [
    "Context",
    "ObjectCondition",
    "Param",
    "Tier",
    "PermKitError",
    "PermissionDenied",
    "ConfigurationError",
    "UnknownKey",
    "UnknownCondition",
    "InvalidParams",
    "register_key",
    "register_condition",
    "object_condition",
    "permission_object",
    "object_permissions",
    "apply_permissions",
    "api_permission",
    "field_groups",
    "condition_params",
    "registry",
    "require",
    "apply_scope",
    "require_object",
    "check_object",
    "strip_fields",
    "assert_writable",
    "explain",
    "get_policy",
]


def get_policy():
    from .conf import get_policy as _get

    return _get()


# -- Layer 1 -------------------------------------------------------------


def require(user, key: str) -> None:
    """Endpoint tier. Raise :class:`PermissionDenied` unless permitted."""
    get_policy().require(user, key)


def apply_scope(qs, *, user, key: str):
    """Object tier (read). Narrow ``qs`` to the rows ``user`` may see."""
    return get_policy().apply_scope(qs, user=user, key=key)


def check_object(user, key: str, obj) -> bool:
    """Object tier. True when ``obj`` is within this user's scope for ``key``."""
    return get_policy().check_object(user, key, obj)


def require_object(user, key: str, obj) -> None:
    """Object tier (write). Raise unless ``obj`` is in scope for ``key``."""
    get_policy().require_object(user, key, obj)


def strip_fields(data: Mapping[str, Any], *, user, key: str) -> dict:
    """Field tier (read). Drop fields this user may not see."""
    return get_policy().strip_fields(data, user=user, key=key)


def assert_writable(data: Mapping[str, Any], *, user, key: str) -> None:
    """Field tier (write). Raise if ``data`` touches a field they may not set."""
    get_policy().assert_writable(data, user=user, key=key)


def explain(user, key: str, obj=None):
    """Return a :class:`~permkit.resolver.Trace` explaining a decision."""
    return get_policy().explain(user, key, obj)
