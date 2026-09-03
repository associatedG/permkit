"""Resolving grants once per unit of work.

Every tier asks the store the same question — what may these roles do with
this key? — and a field-stripping loop asks again for every row. The answers
cannot change while one request is being served, so asking once is free.

The property that makes it safe is the *bound*: nothing is cached outside an
explicit scope. Forgetting the middleware costs queries; it can never serve a
grant that has since been revoked.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from permkit import grant_cache, strip_fields
from permkit.cache import GrantCacheMiddleware, is_active
from permkit.conf import reset_policy, set_policy
from permkit.resolver import Policy
from permkit.principals import AttributeRoleResolver

from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


class CountingStore:
    """Wraps a store and counts what it is asked."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = {"endpoint": 0, "object": 0, "field": 0}

    def has_endpoint_grant(self, roles, key):
        self.calls["endpoint"] += 1
        return self.inner.has_endpoint_grant(roles, key)

    def object_grants(self, roles, key):
        self.calls["object"] += 1
        return self.inner.object_grants(roles, key)

    def field_grants(self, roles, key):
        self.calls["field"] += 1
        return self.inner.field_grants(roles, key)


@pytest.fixture
def counting(store, grants):
    counter = CountingStore(store)
    set_policy(Policy(store=counter, principals=AttributeRoleResolver("role")))
    yield counter
    reset_policy()


def render(user):
    """One page: a scoped list, then field stripping per row."""
    rows = list(widget_list(fetched_by=user))
    for row in rows:
        strip_fields({"secret_price": row.secret_price}, user=user, key="widget.view")
    return rows


# -- the N+1 --------------------------------------------------------------


def test_field_grants_are_fetched_once_per_page(counting, widgets, keeper_kho1):
    """Not once per row, which is what made a long page expensive."""
    with grant_cache():
        rows = render(keeper_kho1)

    assert len(rows) == 3
    assert counting.calls["field"] == 1


def test_every_kind_of_grant_is_fetched_once(counting, widgets, keeper_kho1):
    with grant_cache():
        render(keeper_kho1)

    assert counting.calls == {"endpoint": 1, "object": 1, "field": 1}


def test_without_a_scope_nothing_is_cached(counting, widgets, keeper_kho1):
    """Behaviour is exactly what it was, which is why forgetting is safe."""
    render(keeper_kho1)

    assert counting.calls["field"] == 3


# -- the bound ------------------------------------------------------------


def test_a_new_scope_sees_a_change_made_since_the_last_one(
    counting, store, widgets, keeper_kho1
):
    """The reason revocation stays correct: the cache dies with the scope."""
    with grant_cache():
        assert widget_list(fetched_by=keeper_kho1).count() == 3

    store._object.clear()  # revoke every object grant

    with grant_cache():
        assert widget_list(fetched_by=keeper_kho1).count() == 0


def test_the_scope_closes_even_when_the_body_raises(counting, widgets, keeper_kho1):
    with pytest.raises(RuntimeError):
        with grant_cache():
            render(keeper_kho1)
            raise RuntimeError("boom")

    assert not is_active()


def test_scopes_nest_without_poisoning_the_caller(counting, widgets, keeper_kho1):
    with grant_cache():
        render(keeper_kho1)
        assert counting.calls["field"] == 1
        with grant_cache():
            render(keeper_kho1)
            assert counting.calls["field"] == 2  # inner scope, its own dict
        render(keeper_kho1)
        assert counting.calls["field"] == 2  # outer dict restored, still warm
    assert not is_active()


def test_two_users_in_one_scope_do_not_share(counting, widgets, make_user):
    """The key includes the roles, so one person's grants are not another's."""
    keeper = make_user(role="w_keeper", warehouse="KHO_1")
    viewer = make_user(role="w_viewer")

    with grant_cache():
        assert widget_list(fetched_by=keeper).count() == 3
        assert widget_list(fetched_by=viewer).count() == 4

    assert counting.calls["object"] == 2


def test_two_keys_in_one_scope_do_not_share(counting, widgets, keeper_kho1):
    """widget.view and widget.update are different keys and different answers."""
    from .dummy.selectors import widget_writable

    with grant_cache():
        assert widget_list(fetched_by=keeper_kho1).count() == 3
        assert widget_writable(fetched_by=keeper_kho1).count() == 2

    assert counting.calls["object"] == 2


# -- answers are unchanged ------------------------------------------------


@pytest.mark.parametrize("scoped", [True, False])
def test_the_cache_changes_no_answer(counting, widgets, keeper_kho1, viewer, scoped):
    import contextlib

    with (grant_cache() if scoped else contextlib.nullcontext()):
        assert widget_list(fetched_by=keeper_kho1).count() == 3
        assert widget_list(fetched_by=viewer).count() == 4
        assert strip_fields(
            {"secret_price": 1}, user=viewer, key="widget.view"
        ) == {}


# -- the middleware -------------------------------------------------------


def test_the_middleware_opens_a_scope():
    seen = {}

    def view(request):
        seen["active"] = is_active()
        return "response"

    assert GrantCacheMiddleware(view)(RequestFactory().get("/")) == "response"
    assert seen["active"] is True
    assert not is_active(), "the scope must close with the response"


def test_the_middleware_closes_the_scope_when_the_view_raises():
    def view(request):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        GrantCacheMiddleware(view)(RequestFactory().get("/"))

    assert not is_active()


def test_a_real_request_is_scoped(counting, widgets, keeper_kho1, client):
    """End to end: the middleware is installed and the page renders once."""
    client.force_login(keeper_kho1)

    response = client.get("/widgets/")

    assert response.status_code == 200
    assert len(response.json()) == 3
    assert counting.calls["field"] == 1
