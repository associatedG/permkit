"""Layer 2 — DRF ergonomics.

Thin wrappers over the Layer 1 functions.  Nothing here implements policy; it
only routes DRF's hook points into the resolver.  Projects that use plain
``APIView`` plus selectors can ignore this module entirely and call the
functions directly — which is the point of splitting the layers.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from . import apply_scope, assert_writable, check_object, get_policy, strip_fields
from .declare import field_groups
from .registry import registry
from .exceptions import ConfigurationError, PermissionDenied, UnknownKey


def exception_handler(exc, context):
    """Translate permkit's DRF-free exceptions into HTTP responses.

    Layer 1 deliberately does not import DRF, so it raises its own
    :class:`~permkit.exceptions.PermissionDenied`.  Without this handler DRF
    would not recognise it and a denial would surface as a 500 instead of a
    403.  Install as ``REST_FRAMEWORK["EXCEPTION_HANDLER"]``.

    Domain invariants are deliberately *not* translated here — they are not
    authorization failures and should map to 409/422 by the project's own
    handler.
    """
    # Imported lazily: ``rest_framework.views`` resolves
    # DEFAULT_PERMISSION_CLASSES on import, which points back at this module.
    from rest_framework import exceptions as drf_exceptions
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, PermissionDenied):
        exc = drf_exceptions.PermissionDenied(str(exc))
    return drf_exception_handler(exc, context)


class DenyAll(BasePermission):
    """Deny-by-default for ``DEFAULT_PERMISSION_CLASSES``.

    Install this globally so a view that declares no permission is closed
    rather than open.  DRF's own default is ``AllowAny``, which turns a
    forgotten declaration into a public endpoint.
    """

    def has_permission(self, request, view) -> bool:
        return False


class PermissionRequired(BasePermission):
    """Endpoint tier, plus the object tier when the view declares a key.

    Usable either pre-configured (``PermissionRequired("order.view")``) or
    bare (``PermissionRequired``), in which case the key comes from the view's
    ``permission_key`` or ``permission_keys[action]``.
    """

    def __init__(self, key: str | None = None) -> None:
        self.key = key

    def __call__(self):
        # DRF instantiates each entry of ``permission_classes``; returning
        # self lets a pre-configured instance be used in that list.
        return self

    # -- key resolution ---------------------------------------------------

    def _key_for(self, view) -> str | None:
        if self.key:
            return self.key
        key = getattr(view, "permission_key", None)
        if key:
            return key
        keys = getattr(view, "permission_keys", None)
        action = getattr(view, "action", None)
        if keys and action:
            return keys.get(action)
        return getattr(view, "permission_action", None)

    # -- hooks ------------------------------------------------------------

    def has_permission(self, request, view) -> bool:
        key = self._key_for(view)
        if not key:
            # An undeclared view is a configuration bug; deny rather than
            # quietly allow.
            return False
        try:
            return get_policy().check_endpoint(request.user, key)
        except UnknownKey:
            return False

    def has_object_permission(self, request, view, obj) -> bool:
        key = getattr(view, "object_key", None) or self._key_for(view)
        if not key:
            return False
        try:
            return check_object(request.user, key, obj)
        except ConfigurationError:
            # Not a scopable key — the endpoint tier already decided.
            return True
        except UnknownKey:
            return False


class ScopedQuerysetMixin:
    """Narrow a generic view's queryset to the rows the user may read.

    Because ``GenericAPIView.get_object()`` draws from ``get_queryset()``, this
    also makes the detail route 404 for out-of-scope rows — the list and the
    detail cannot disagree, since both come from one ``Q``.
    """

    read_key: str = ""

    def get_queryset(self):
        qs = super().get_queryset()
        key = self.read_key or getattr(self, "permission_key", "")
        if not key:
            raise ConfigurationError(
                f"{type(self).__name__} uses ScopedQuerysetMixin but declares "
                f"no read_key."
            )
        return apply_scope(qs, user=self.request.user, key=key)


class FieldPermissionMixin:
    """Declare *and* enforce the field tier on a serializer.

    The serializer is where a field's sensitivity is actually known, so it is
    where the groups are declared::

        class WidgetSerializer(FieldPermissionMixin, ModelSerializer):
            permission_object = "widget"
            permission_fields = {
                "money": ["secret_price"],   # a group
                "notes": "notes",            # a group of one
            }

    Groups rather than bare column names, because an admin picks *"Money"*
    rather than ``secret_price`` — and adding a column to a group updates every
    abstract role that already granted it.

    Read and write keys are derived from ``permission_object`` unless set
    explicitly. They stay separate because the tiers are: a role may set
    ``notes`` while never seeing ``price``, and one PATCH exercises both — the
    payload validated against the write key, the response stripped against the
    read key.
    """

    #: Which object these fields belong to. Registration is skipped without it.
    permission_object: str = ""
    #: ``{group_key: field or [fields]}`` — declared by the class that owns them.
    permission_fields: dict = {}
    #: ``{"crate": "crate.view"}`` — foreign keys whose target rows are governed
    #: by another key.  The serializer is where the payload's shape is known.
    permission_references: dict = {}
    read_action: str = "view"
    write_action: str = "update"

    read_permission_key: str = ""
    write_permission_key: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        own = cls.__dict__
        obj = own.get("permission_object") or cls.permission_object
        if not obj:
            return

        # Only the class that declares the groups registers them; a subclass
        # inheriting them must not re-register.
        if "permission_fields" in own and own["permission_fields"]:
            field_groups(obj, own["permission_fields"])
        if "permission_references" in own and own["permission_references"]:
            registry.register_references(obj, own["permission_references"])

        if "read_permission_key" not in own:
            cls.read_permission_key = f"{obj}.{cls.read_action}"
        if "write_permission_key" not in own:
            cls.write_permission_key = f"{obj}.{cls.write_action}"

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.read_permission_key:
            data = strip_fields(
                data, user=self._user(), key=self.read_permission_key
            )
        return data

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.write_permission_key:
            assert_writable(
                attrs, user=self._user(), key=self.write_permission_key
            )
        return attrs
