"""State invariants are not permissions, and no grant may bypass them.

The production bug this guards against: a state lock ("completed orders are
frozen") implemented *inside* a permission hook, where an earlier
``return True`` for a privileged role skipped the check entirely.  Keeping the
invariant in the service makes that ordering mistake unrepresentable.
"""

from __future__ import annotations

import pytest

from permkit import PermissionDenied

from .dummy.services import WidgetLocked, widget_update

pytestmark = pytest.mark.django_db


def test_locked_widget_rejects_every_role(
    policy, grants, widgets, admin_user, keeper_kho1
):
    locked = widgets["kho1_locked"]

    # Both principals pass the permission tier for this row ...
    for actor in (admin_user, keeper_kho1):
        # ... and are still refused, by the invariant rather than by policy.
        with pytest.raises(WidgetLocked):
            widget_update(actor=actor, widget=locked, notes="nope")


def test_superuser_cannot_bypass_an_invariant(
    policy, grants, widgets, make_user
):
    """Superuser bypasses *authorization*. It is not a licence to break state."""
    root = make_user(role="", username="root")
    root.is_superuser = True
    root.save()

    with pytest.raises(WidgetLocked):
        widget_update(actor=root, widget=widgets["kho1_locked"], notes="nope")


def test_permission_and_invariant_are_distinguishable(
    policy, grants, widgets, keeper_kho1
):
    """Callers must be able to tell "you may not" from "nobody may, now".

    They map to different HTTP statuses (403 vs 409/422), so they cannot share
    an exception type.
    """
    # Out of write scope → permission failure.
    with pytest.raises(PermissionDenied):
        widget_update(
            actor=keeper_kho1, widget=widgets["kho1_unassigned"], notes="x"
        )

    # In scope, but the row is locked → invariant failure.
    with pytest.raises(WidgetLocked):
        widget_update(actor=keeper_kho1, widget=widgets["kho1_locked"], notes="x")

    assert not issubclass(WidgetLocked, PermissionDenied)


def test_invariant_runs_after_permission(policy, grants, widgets, outsider):
    """An unauthorised caller learns nothing about the row's state."""
    with pytest.raises(PermissionDenied):
        widget_update(actor=outsider, widget=widgets["kho1_locked"], notes="x")
