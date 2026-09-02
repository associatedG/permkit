"""Layer 2 — decorators for the service/selector layer.

Services are where business logic lives, so they are also where the
non-HTTP call paths (Celery tasks, management commands, admin actions) enter
the domain.  Guarding them here covers those paths, which no DRF hook reaches.
"""

from __future__ import annotations

from functools import wraps
from typing import Callable

from . import require, require_object
from .exceptions import PermissionDenied


def requires(key: str, *, user_kwarg: str = "actor") -> Callable:
    """Endpoint-tier guard for a keyword-only service.

    ::

        @requires("widget.update")
        def widget_update(*, actor: User, widget: Widget, **data): ...
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if user_kwarg not in kwargs:
                raise TypeError(
                    f"{fn.__name__}() is guarded by @requires and must be "
                    f"called with the {user_kwarg!r} keyword argument."
                )
            require(kwargs[user_kwarg], key)
            return fn(*args, **kwargs)

        wrapper.permission_key = key
        return wrapper

    return decorator


def requires_object(
    key: str, *, user_kwarg: str = "actor", obj_kwarg: str
) -> Callable:
    """Object-tier guard for a keyword-only service.

    ::

        @requires_object("widget.update", obj_kwarg="widget")
        def widget_update(*, actor: User, widget: Widget, **data): ...
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for name in (user_kwarg, obj_kwarg):
                if name not in kwargs:
                    raise TypeError(
                        f"{fn.__name__}() is guarded by @requires_object and "
                        f"must be called with the {name!r} keyword argument."
                    )
            require_object(kwargs[user_kwarg], key, kwargs[obj_kwarg])
            return fn(*args, **kwargs)

        wrapper.permission_key = key
        return wrapper

    return decorator


__all__ = ["requires", "requires_object", "PermissionDenied"]
