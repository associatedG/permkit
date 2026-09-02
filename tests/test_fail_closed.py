"""Everything that must default to "no"."""

from __future__ import annotations

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from permkit import PermissionDenied, require
from permkit.drf import PermissionRequired
from permkit.exceptions import InvalidParams, UnknownKey

pytestmark = pytest.mark.django_db


def test_unregistered_key_raises_in_the_core(policy, grants, admin_user):
    """A typo in config is a configuration bug, and must be loud.

    The failure mode being avoided: ``config.get(resource, {}).get(action,
    False)``, where a misspelling is indistinguishable from a deliberate deny.
    """
    with pytest.raises(UnknownKey):
        policy.check_endpoint(admin_user, "widget.viwe")


def test_unregistered_key_denies_at_the_boundary(policy, grants, admin_user):
    """The core raises; the DRF adapter converts that into a denial."""

    class View(APIView):
        permission_key = "widget.viwe"

    request = APIRequestFactory().get("/")
    request.user = admin_user
    assert PermissionRequired().has_permission(request, View()) is False


def test_view_that_declares_no_key_is_closed(policy, grants, admin_user):
    class View(APIView):
        pass

    request = APIRequestFactory().get("/")
    request.user = admin_user
    assert PermissionRequired().has_permission(request, View()) is False


def test_undeclared_view_is_denied_by_the_global_default(policy, grants, admin_user):
    """``DEFAULT_PERMISSION_CLASSES = [DenyAll]`` — a forgotten declaration
    yields a closed endpoint rather than a public one."""

    class View(APIView):
        def get(self, request):
            return Response({"ok": True})

    request = APIRequestFactory().get("/")
    request.user = admin_user
    response = View.as_view()(request)
    assert response.status_code == 403


def test_role_with_no_grants_is_denied(policy, grants, outsider):
    with pytest.raises(PermissionDenied):
        require(outsider, "widget.view")


def test_user_with_no_role_is_denied(policy, grants, make_user):
    with pytest.raises(PermissionDenied):
        require(make_user(role=""), "widget.view")


def test_anonymous_principal_is_denied(policy, grants):
    with pytest.raises(PermissionDenied):
        require(None, "widget.view")


def test_bad_condition_params_are_rejected(policy, store, widgets, make_user):
    store.grant_endpoint("broken", "widget.view")
    store.grant_object(
        "broken",
        "widget.view",
        name="broken-grant",
        conditions=[{"condition": "widget.status_in", "params": {"nonsense": 1}}],
    )
    user = make_user(role="broken")

    with pytest.raises(InvalidParams):
        policy.scope(user, "widget.view")


def test_superuser_bypass_is_explicit_and_switchable(
    grants, store, widgets, make_user
):
    """Bypass is a declared setting, not an incidental ``if is_superuser``."""
    from permkit.principals import AttributeRoleResolver
    from permkit.resolver import Policy

    root = make_user(role="", username="root2")
    root.is_superuser = True
    root.save()

    permissive = Policy(
        store=store, principals=AttributeRoleResolver("role"), superuser_bypass=True
    )
    assert permissive.check_endpoint(root, "widget.view") is True

    strict = Policy(
        store=store, principals=AttributeRoleResolver("role"), superuser_bypass=False
    )
    assert strict.check_endpoint(root, "widget.view") is False


def test_an_object_grant_without_an_endpoint_grant_denies(
    policy, store, make_user
):
    """Not being allowed the action at all implies no rows to do it to.

    A role configured with an object grant but no endpoint grant used to pass
    ``require_object`` — the endpoint tier is checked by DRF before the object
    tier, so a caller entering at the service layer (task, command, admin) had
    it checked for them by nothing.
    """
    from permkit import check_object, require_object
    from permkit.resolver import ScopeKind

    from .dummy.models import Widget

    store.grant_object("half", "widget.update", name="half-all")
    actor = make_user(role="half")
    widget = Widget.objects.create(name="w", owner=actor, warehouse="A")

    assert policy.scope(actor, "widget.update").kind is ScopeKind.DENY
    assert check_object(actor, "widget.update", widget) is False
    with pytest.raises(PermissionDenied):
        require_object(actor, "widget.update", widget)
