"""Component-level declaration — the developer-facing surface.

Three tiers, each declared where the component lives, never in a central file:

* **endpoint** — ``@api_permission`` on the view or service
* **field** — ``permission_fields`` on the serializer, as named groups
* **object** — ``@object_permissions`` on the selector, plus an explicit
  ``apply_permissions`` call inside it

Everything registered here is what the catalogue sync publishes, and what an
admin later assembles into an abstract role.  Docstrings and labels are not
decoration: they are what the admin UI shows to whoever composes the rules.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Mapping

from django.db.models import Q

from .base import Context, ObjectCondition, Param
from .exceptions import ConfigurationError
from .registry import registry

# Set by @object_permissions before the selector body runs, cleared by
# apply_permissions.  Lets the decorator prove the filters were actually
# applied, so declaring them and enforcing them cannot drift apart.
_PENDING: ContextVar[list | None] = ContextVar("permkit_pending", default=None)


# ------------------------------------------------------------------ object


def permission_object(key: str, *, model=None, label: str = "") -> None:
    """Bind an object key to its model.

    The one fact no component declaration carries: a selector knows the endpoint,
    a serializer knows the fields, a filter knows the condition — none of them
    names the model the object key stands for.  Declare it beside the object's
    filters, which is the file that is already about that object.
    """
    registry.register_object(key, model=model, label=label)


# ---------------------------------------------------------------- endpoint


def api_permission(endpoint_key: str, *, label: str = "") -> Callable:
    """Declare that this component enforces an endpoint.

    The endpoint tier is the simple one: a single yes/no per role, with no
    payload of its own.

    Several components may enforce the same endpoint — a list view and a detail
    view are usually one permission.  The first to declare it supplies the
    label and mode; the rest just name it, and are recorded as further places
    that enforce it.  There is deliberately no second way to say this: a
    component that named a key without declaring it would be invisible to the
    catalogue, and one that declared an endpoint it did not enforce would leave
    an entry an admin can grant and nothing honours.
    """

    def decorator(target):
        registry.register_endpoint(
            endpoint_key,
            label=label,
            target=f"{target.__module__}.{target.__qualname__}",
        )
        # A component may enforce more than one endpoint — a ListCreateAPIView
        # serving a list on GET and a create on POST. Then there is no single
        # key, and leaving whichever decorator ran first would have the
        # enforcement layer silently check an arbitrary one. Both singular
        # attributes are cleared instead, so the component must say which key
        # belongs to which operation through ``permission_keys``.
        #
        # ``__dict__`` rather than getattr: an inherited value belongs to the
        # parent's declaration, not this one.
        own = target.__dict__
        seen = tuple(own.get("permission_endpoints") or ())
        if not seen and own.get("permission_endpoint"):
            seen = (own["permission_endpoint"],)
        endpoints = tuple(dict.fromkeys((*seen, endpoint_key)))
        target.permission_endpoints = endpoints

        if len(endpoints) == 1:
            target.permission_endpoint = endpoint_key
            # The enforcement layer reads ``permission_key``; the declaration
            # already carries it, so the key is never written twice on one class.
            if getattr(target, "permission_key", None) is None:
                target.permission_key = endpoint_key
        else:
            target.permission_endpoint = None
            # Clear it only if a previous decorator is what put it there. A key
            # the developer wrote by hand is a deliberate choice and stands.
            if own.get("permission_key") in seen:
                target.permission_key = None
        return target

    return decorator


# ------------------------------------------------------------------- field


def field_groups(object_key: str, groups: Mapping[str, Any]) -> None:
    """Register named field groups for an object.

    Groups, not bare column names, because an admin should pick *"Money"*
    rather than ``secret_price`` — and adding a column to a group updates
    every abstract role that already granted it.

    A group whose value is a single string is a group of one, for the common
    case where a field needs guarding on its own.
    """
    for group_key, fields in groups.items():
        names = [fields] if isinstance(fields, str) else list(fields)
        registry.register_field_group(object_key, group_key, fields=names)


# ------------------------------------------------------------------ object


def object_permissions(
    object_key: str,
    endpoint_key: str,
    *,
    filters: Mapping[str, Any] | None = None,
    actor_kwarg: str = "fetched_by",
) -> Callable:
    """Declare a selector as a permission site for ``object_key.endpoint_key``.

    ``filters`` maps a filter key to either a ``Q`` (a static condition) or a
    callable taking the request :class:`~permkit.base.Context` and returning
    one.  Filters register under the **object**, not this selector, so every
    read path for that object shares them.

    The decorator only registers.  The selector itself decides *where* the
    filter lands by calling :func:`apply_permissions` — necessary because a
    real query may need scoping before an aggregate, inside a subquery, or
    between joins, none of which a decorator wrapping the return value can do.

    Registering without enforcing is still impossible: the wrapper raises if
    the body returns without having applied the filters.
    """

    def decorator(fn: Callable) -> Callable:
        for condition_key, spec in (filters or {}).items():
            _register_condition(object_key, condition_key, spec)

        registry.register_scope_point(
            object_key,
            endpoint_key,
            target=f"{fn.__module__}.{fn.__qualname__}",
        )

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if actor_kwarg not in kwargs:
                raise TypeError(
                    f"{fn.__name__}() is a permission site and must be called "
                    f"with the {actor_kwarg!r} keyword argument."
                )
            token = _PENDING.set(
                {
                    "key": f"{object_key}.{endpoint_key}",
                    "actor": kwargs[actor_kwarg],
                    "applied": False,
                }
            )
            try:
                result = fn(*args, **kwargs)
                if not _PENDING.get()["applied"]:
                    raise ConfigurationError(
                        f"{fn.__module__}.{fn.__qualname__} declares object "
                        f"permissions for {object_key}.{endpoint_key} but never "
                        f"called apply_permissions(). Declaring filters "
                        f"without applying them means an administrator can "
                        f"configure a rule that silently does nothing."
                    )
                return result
            finally:
                _PENDING.reset(token)

        wrapper.permission_object = object_key
        wrapper.permission_endpoint = endpoint_key
        return wrapper

    return decorator


def apply_permissions(qs, *, actor=None):
    """Apply the acting role's filters to ``qs``, inside a permission site.

    Call it wherever the narrowing belongs in the query. ``actor`` defaults to
    the one the enclosing :func:`object_permissions` site was called with.
    """
    from . import apply_scope

    pending = _PENDING.get()
    if pending is None:
        raise ConfigurationError(
            "apply_permissions() was called outside a function decorated with "
            "@object_permissions. The filters to apply come from that "
            "declaration."
        )
    pending["applied"] = True
    return apply_scope(qs, user=actor or pending["actor"], key=pending["key"])


# ----------------------------------------------------------------- helpers


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().splitlines()[0].strip() if doc else ""


def _register_condition(object_key: str, condition_key: str, spec: Any) -> None:
    """Turn a ``Q`` or a callable into a registered condition."""
    if isinstance(spec, Q):
        as_q = lambda self, ctx, **kw: spec  # noqa: E731
        label = f"{condition_key.replace('_', ' ').capitalize()}"
        params: Mapping[str, Param] = {}
        multi_valued = False
    elif callable(spec):
        as_q = lambda self, ctx, **kw: spec(ctx, **kw)  # noqa: E731
        label = _first_line(spec.__doc__) or condition_key
        params = getattr(spec, "permission_params", {})
        multi_valued = getattr(spec, "permission_multi_valued", False)
    else:
        raise ConfigurationError(
            f"Filter {object_key}.{condition_key} must be a Q or a callable "
            f"returning one, got {type(spec).__name__}."
        )

    condition_id = f"{object_key}.{condition_key}"
    if registry.has_condition(condition_id):
        return  # already declared by another site for the same object

    condition_cls = type(
        f"Filter_{object_key}_{condition_key}",
        (ObjectCondition,),
        {"as_q": as_q, "__doc__": label},
    )
    registry.register_condition(
        condition_id,
        params=params,
        multi_valued=multi_valued,
        object_key=object_key,
    )(condition_cls)


def object_condition(
    object_key: str,
    condition_key: str,
    *,
    params: Mapping[str, Param] | None = None,
    multi_valued: bool = False,
) -> Callable:
    """Register a standalone filter function under an object and filter key.

    Equivalent to naming the function in a selector's ``filters=`` mapping;
    use whichever reads better for the rule at hand.
    """

    def decorator(fn: Callable) -> Callable:
        fn.permission_params = dict(params or {})
        fn.permission_multi_valued = multi_valued
        _register_condition(object_key, condition_key, fn)
        fn.object_key = object_key
        fn.condition_key = condition_key
        fn.condition_id = f"{object_key}.{condition_key}"
        return fn

    return decorator


def condition_params(**params: Param) -> Callable:
    """Declare open params on a filter function referenced from a selector."""

    def decorator(fn: Callable) -> Callable:
        fn.permission_params = dict(params)
        return fn

    return decorator


__all__ = [
    "permission_object",
    "api_permission",
    "field_groups",
    "object_permissions",
    "apply_permissions",
    "object_condition",
    "condition_params",
    "Context",
    "Param",
]
