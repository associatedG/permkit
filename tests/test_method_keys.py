"""One component, two endpoints.

A ``ListCreateAPIView`` serves a list on GET and a create on POST. Those are
two keys, deliberately — read and write never share a grant. But a generic view
has no DRF ``action``, so nothing but the HTTP method separates them, and the
enforcement layer has to be able to ask.

The failure this replaces: stacking two ``@api_permission`` decorators left
``permission_key`` set to whichever ran first, and the endpoint check silently
asked about that one.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from permkit import api_permission, registry
from permkit.drf import PermissionRequired

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _endpoints():
    """Register the object and the two keys this module needs, once.

    The object matters: ``registry.key()`` assembles a key from the object that
    binds the model, so an endpoint alone does not resolve.
    """
    from .dummy.models import Widget

    if not registry.has_object("thing"):
        registry.register_object("thing", model=Widget, label="Thing")
    for key in ("thing.view", "thing.create"):
        if key not in registry.endpoints:
            registry.register_endpoint(key, label=key, target="tests.method_keys")


class ListCreate:
    """A generic view: two operations, no DRF ``action`` attribute."""

    permission_keys = {"GET": "thing.view", "POST": "thing.create"}


class ViewSetish:
    """A ViewSet: DRF sets ``action`` per request."""

    permission_keys = {"list": "thing.view", "create": "thing.create"}
    action = None


def key_for(view, method="GET"):
    request = RequestFactory().generic(method, "/")
    return PermissionRequired()._key_for(view, request)


# -- resolution -----------------------------------------------------------


@pytest.mark.parametrize(
    "method,expected", [("GET", "thing.view"), ("POST", "thing.create")]
)
def test_a_generic_view_resolves_by_http_method(method, expected):
    assert key_for(ListCreate(), method) == expected


def test_a_viewset_still_resolves_by_action():
    """The ViewSet path must keep working — action wins where it exists."""
    view = ViewSetish()
    view.action = "create"

    assert key_for(view, "GET") == "thing.create"


def test_permission_keys_beats_a_single_permission_key():
    """The more specific declaration wins; otherwise it could never take effect."""

    class Both:
        permission_key = "thing.view"
        permission_keys = {"POST": "thing.create"}

    assert key_for(Both(), "POST") == "thing.create"
    # Falls back for a method the mapping does not name.
    assert key_for(Both(), "GET") == "thing.view"


def test_an_explicit_key_on_the_permission_class_still_wins():
    assert PermissionRequired("thing.view")._key_for(ListCreate(), None) == "thing.view"


def test_no_request_is_survivable():
    """has_object_permission and older call sites may not pass one."""

    class Single:
        permission_key = "thing.view"

    assert PermissionRequired()._key_for(Single(), None) == "thing.view"


# -- stacking -------------------------------------------------------------


def test_two_endpoints_on_one_component_leave_no_arbitrary_key():
    """The bug: whichever decorator ran first became *the* key."""

    @api_permission("thing.view")
    @api_permission("thing.create")
    class Stacked:
        pass

    assert Stacked.permission_key is None
    assert {"thing.view", "thing.create"} <= set(registry.endpoints)


def test_one_endpoint_still_sets_the_key():
    """The ordinary single-endpoint view is untouched."""

    @api_permission("thing.view")
    class Single:
        pass

    assert Single.permission_key == "thing.view"


def test_a_stacked_view_denies_until_it_says_which_key_is_which(make_user, policy):
    """Fail closed: no key means deny, not a guess."""

    @api_permission("thing.view")
    @api_permission("thing.create")
    class Stacked:
        pass

    request = RequestFactory().get("/")
    request.user = make_user(role="w_admin")

    assert PermissionRequired().has_permission(request, Stacked()) is False


def test_declaring_permission_keys_makes_the_stacked_view_work(
    store, make_user, policy
):
    @api_permission("thing.view")
    @api_permission("thing.create")
    class Stacked:
        permission_keys = {"GET": "thing.view", "POST": "thing.create"}

    store.grant_endpoint("w_reader", "thing.view")
    user = make_user(role="w_reader")

    get = RequestFactory().get("/")
    post = RequestFactory().post("/")
    get.user = post.user = user

    assert PermissionRequired().has_permission(get, Stacked()) is True
    assert PermissionRequired().has_permission(post, Stacked()) is False
