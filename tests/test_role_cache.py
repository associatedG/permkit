"""Resolving a user's roles once per user object rather than once per check.

Every tier asks "who is this?", and a list view asks again for each row it
strips fields on. With the default resolver that is an attribute read and
costs nothing. With a resolver that reads the database — roles in their own
table, a profile, a claim — it was a query per check.

The cache hangs on the user *instance*, which is the whole reason it needs no
invalidation scheme: a request builds one, a task builds one, and the next one
starts clean. Nothing outlives the object it is attached to.
"""

from __future__ import annotations

import pytest

from permkit import clear_role_cache
from permkit.principals import AttributeRoleResolver
from permkit.resolver import Policy
from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


class CountingResolver:
    """Wraps the real resolver and counts how often it is asked."""

    def __init__(self):
        self.inner = AttributeRoleResolver("role")
        self.calls = 0

    def roles_for(self, user):
        self.calls += 1
        return self.inner.roles_for(user)


@pytest.fixture
def counting(store, grants):
    from permkit.conf import reset_policy, set_policy

    resolver = CountingResolver()
    set_policy(Policy(store=store, principals=resolver))
    yield resolver
    reset_policy()


def test_the_resolver_is_asked_once_per_user_object(counting, widgets, keeper_kho1):
    """Not once per check — that is the whole point."""
    rows = list(widget_list(fetched_by=keeper_kho1))
    from permkit import strip_fields

    for row in rows:
        strip_fields({"secret_price": 1}, user=keeper_kho1, key="widget.view")

    assert rows, "fixture should return rows"
    assert counting.calls == 1


def test_a_different_user_object_is_resolved_again(counting, widgets, make_user):
    """The cache is per instance, so it cannot leak between two people."""
    a = make_user(role="w_keeper", warehouse="KHO_1")
    b = make_user(role="w_keeper", warehouse="KHO_2")

    widget_list(fetched_by=a).count()
    widget_list(fetched_by=b).count()

    assert counting.calls == 2


def test_a_refetched_user_is_resolved_again(counting, widgets, keeper_kho1):
    """Which is what makes revocation between requests correct without a scheme.

    A new request loads a new user object, so a role changed in the meantime is
    read fresh. There is nothing to invalidate.
    """
    from .dummy.models import User

    widget_list(fetched_by=keeper_kho1).count()
    refetched = User.objects.get(pk=keeper_kho1.pk)
    widget_list(fetched_by=refetched).count()

    assert counting.calls == 2


def test_the_cache_can_be_turned_off(store, grants, widgets, keeper_kho1):
    from permkit.conf import reset_policy, set_policy

    resolver = CountingResolver()
    set_policy(Policy(store=store, principals=resolver, cache_roles=False))
    try:
        widget_list(fetched_by=keeper_kho1).count()
        widget_list(fetched_by=keeper_kho1).count()
        assert resolver.calls > 1
    finally:
        reset_policy()


def test_clearing_forces_a_fresh_read(counting, widgets, keeper_kho1):
    """For a long-running process that changes a role and keeps going."""
    widget_list(fetched_by=keeper_kho1).count()
    assert counting.calls == 1

    clear_role_cache(keeper_kho1)
    widget_list(fetched_by=keeper_kho1).count()

    assert counting.calls == 2


def test_a_role_change_on_a_held_user_takes_effect_after_clearing(
    counting, widgets, keeper_kho1
):
    assert widget_list(fetched_by=keeper_kho1).count() == 3

    keeper_kho1.role = "w_outsider"
    clear_role_cache(keeper_kho1)

    assert widget_list(fetched_by=keeper_kho1).count() == 0


def test_an_object_with_no_dict_still_resolves(store, grants):
    """A __slots__ principal opts out of the cache rather than raising."""
    from permkit.conf import reset_policy, set_policy

    class Slotted:
        __slots__ = ("role", "is_superuser")

        def __init__(self):
            self.role = "w_viewer"
            self.is_superuser = False

    policy = Policy(store=store, principals=AttributeRoleResolver("role"))
    set_policy(policy)
    try:
        assert policy.roles_for(Slotted()) == ["w_viewer"]
    finally:
        reset_policy()


def test_no_user_is_handled(store, grants):
    policy = Policy(store=store, principals=AttributeRoleResolver("role"))

    assert policy.roles_for(None) == []


def test_clearing_an_object_that_was_never_cached_is_harmless():
    class Slotted:
        __slots__ = ()

    clear_role_cache(Slotted())  # must not raise


@pytest.mark.parametrize("cache_roles", [True, False])
def test_caching_does_not_change_any_answer(
    store, grants, widgets, make_user, cache_roles
):
    """The point is fewer lookups, not different answers."""
    from permkit.conf import reset_policy, set_policy

    set_policy(
        Policy(
            store=store,
            principals=AttributeRoleResolver("role"),
            cache_roles=cache_roles,
        )
    )
    try:
        keeper = make_user(role="w_keeper", warehouse="KHO_1")
        viewer = make_user(role="w_viewer")
        assert widget_list(fetched_by=keeper).count() == 3
        assert widget_list(fetched_by=viewer).count() == 4
    finally:
        reset_policy()
