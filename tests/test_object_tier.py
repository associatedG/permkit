"""Object tier: row scoping, and the invariants that keep it honest."""

from __future__ import annotations

import pytest

from permkit import check_object
from permkit.resolver import ScopeKind

from .dummy.models import Widget
from .dummy.selectors import widget_get, widget_list

pytestmark = pytest.mark.django_db


def test_list_and_detail_can_never_disagree(
    policy, grants, widgets, admin_user, keeper_kho1, keeper_kho2, viewer, outsider
):
    """The bug class this design exists to make impossible.

    For every role and every row: if the row is absent from the list it must
    also be unreachable by id, and vice versa.  Both paths compile from one
    ``Q``, so there is no way for them to drift.
    """
    for user in (admin_user, keeper_kho1, keeper_kho2, viewer, outsider):
        visible = set(widget_list(fetched_by=user).values_list("pk", flat=True))

        for widget in Widget.objects.all():
            if widget.pk in visible:
                assert widget_get(fetched_by=user, pk=widget.pk).pk == widget.pk
            else:
                with pytest.raises(Widget.DoesNotExist):
                    widget_get(fetched_by=user, pk=widget.pk)


def test_zero_grants_denies_every_row(policy, grants, widgets, outsider):
    """A principal with no grants must see nothing — not everything.

    Building the filter by OR-ing an empty list of grants would yield ``Q()``,
    which in Django matches the whole table.  DENY is therefore a distinct
    state, never an empty Q.
    """
    assert policy.scope(outsider, "widget.view").kind is ScopeKind.DENY
    assert widget_list(fetched_by=outsider).count() == 0
    assert Widget.objects.count() == 4  # the rows exist; they are just not his


def test_unconditional_grant_widens_rather_than_narrows(
    policy, store, widgets, make_user
):
    """Django collapses ``Q() | Q(x=1)`` to ``Q(x=1)``.

    Naively OR-ing an unconditional grant would therefore *shrink* the result
    to the conditional grant's rows.  It must short-circuit to ALL instead.
    """
    store.grant_endpoint("mixed", "widget.view")
    store.grant_object(
        "mixed",
        "widget.view",
        name="mixed-narrow",
        conditions=[
            {"condition": "widget.warehouse"}
        ],
    )
    store.grant_object("mixed", "widget.view", name="mixed-all")  # unconditional

    user = make_user(role="mixed", warehouse="KHO_1")

    assert policy.scope(user, "widget.view").kind is ScopeKind.ALL
    assert widget_list(fetched_by=user).count() == Widget.objects.count()


def test_conditions_within_a_grant_intersect(
    policy, grants, widgets, keeper_kho1
):
    """``warehouse AND assigned`` — the rule the hand-written code got wrong.

    The production bug this models granted *every* keeper write access to
    *every* order; here the two conditions must both hold.
    """
    assert check_object(keeper_kho1, "widget.update", widgets["kho1_assigned"])
    assert not check_object(keeper_kho1, "widget.update", widgets["kho1_unassigned"])
    assert not check_object(keeper_kho1, "widget.update", widgets["kho2"])


def test_read_scope_and_write_scope_are_configured_separately(
    policy, grants, widgets, keeper_kho1
):
    """Seeing a row and being able to edit it are different questions."""
    readable = set(widget_list(fetched_by=keeper_kho1).values_list("name", flat=True))
    assert readable == {"kho1-assigned", "kho1-unassigned", "kho1-locked"}

    # ... but only the assigned ones are writable.
    writable = {
        w.name
        for w in Widget.objects.all()
        if check_object(keeper_kho1, "widget.update", w)
    }
    assert writable == {"kho1-assigned", "kho1-locked"}


def test_scope_isolates_users_of_the_same_role(
    policy, grants, widgets, keeper_kho1, keeper_kho2
):
    """Two users, one role, different rows — scope is data, not role alone."""
    kho1 = set(widget_list(fetched_by=keeper_kho1).values_list("warehouse", flat=True))
    kho2 = set(widget_list(fetched_by=keeper_kho2).values_list("warehouse", flat=True))

    assert kho1 == {"KHO_1"}
    assert kho2 == {"KHO_2"}
