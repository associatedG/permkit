"""Services that actually do something — orchestration, not one-line wrappers.

The earlier service tests exercised single-write functions.  These cover what
a real service does: several writes, a selector for its rows, a nested service
call, a transaction around all of it, and a non-HTTP entrypoint.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from permkit import PermissionDenied

from .dummy.models import Crate, Widget
from .dummy.services import widget_bulk_annotate, widget_transfer

pytestmark = pytest.mark.django_db


@pytest.fixture
def target_crate(db):
    crate, _ = Crate.objects.get_or_create(name="destination")
    return crate


# -- multi-write + transaction ------------------------------------------


def test_transfer_moves_every_widget_when_all_are_permitted(
    policy, grants, widgets, admin_user, target_crate
):
    movable = list(Widget.objects.exclude(status=Widget.Status.LOCKED))
    moved = widget_transfer(
        actor=admin_user, widgets=movable, to_crate=target_crate
    )
    assert moved == 3
    assert Widget.objects.filter(crate=target_crate).count() == 3


def test_a_locked_row_aborts_and_rolls_back_the_batch(
    policy, grants, widgets, admin_user, target_crate
):
    """An invariant failure inside a multi-write service must undo the batch.

    Ordered so two permitted widgets are written before the locked one is
    reached — without the transaction they would stay moved.
    """
    from .dummy.services import WidgetLocked

    batch = [widgets["kho1_assigned"], widgets["kho2"], widgets["kho1_locked"]]
    with pytest.raises(WidgetLocked):
        widget_transfer(actor=admin_user, widgets=batch, to_crate=target_crate)

    assert Widget.objects.filter(crate=target_crate).count() == 0


def test_denial_midway_rolls_back_the_whole_transfer(
    policy, grants, widgets, keeper_kho1, target_crate
):
    """The property that makes a multi-write service safe.

    ``keeper_kho1`` may write the two widgets assigned to them but not
    ``kho1-unassigned``.  Ordering the batch so a permitted widget is written
    *before* the denial means a non-atomic implementation would leave that
    first write committed — a permission failure that mutated data.
    """
    permitted = widgets["kho1_assigned"]
    denied = widgets["kho1_unassigned"]
    original = permitted.crate

    with pytest.raises(PermissionDenied):
        widget_transfer(
            actor=keeper_kho1, widgets=[permitted, denied], to_crate=target_crate
        )

    permitted.refresh_from_db()
    denied.refresh_from_db()
    assert permitted.crate == original, "earlier write must have rolled back"
    assert denied.crate != target_crate
    assert Widget.objects.filter(crate=target_crate).count() == 0


def test_reference_scope_denial_also_rolls_back(
    policy, store, widgets, make_user, target_crate
):
    """A forbidden *destination* must undo the writes already made.

    The FK check fires per widget, so the first row is written before the
    second one's reference check refuses.
    """
    store.grant_endpoint("mover", "widget.update")
    store.grant_endpoint("mover", "crate.view")
    store.grant_object("mover", "widget.update", name="mover-all")
    store.grant_object(
        "mover",
        "crate.view",
        name="mover-crates",
        conditions=[
            {"condition": "crate.named", "params": {"names": ["crate-1"]}}
        ],
    )
    store.grant_field(
        "mover", "widget.update", name="mover-write", allowed_fields=["crate", "notes"]
    )
    actor = make_user(role="mover")

    with pytest.raises(PermissionDenied, match="outside your scope"):
        widget_transfer(
            actor=actor, widgets=list(Widget.objects.all()), to_crate=target_crate
        )

    assert Widget.objects.filter(crate=target_crate).count() == 0


def test_transfer_is_denied_outright_without_the_endpoint_grant(
    policy, grants, widgets, outsider, target_crate
):
    with pytest.raises(PermissionDenied):
        widget_transfer(
            actor=outsider, widgets=list(Widget.objects.all()), to_crate=target_crate
        )
    assert Widget.objects.filter(crate=target_crate).count() == 0


# -- service reaching rows through a selector ---------------------------


def test_bulk_service_only_touches_rows_in_write_scope(
    policy, grants, widgets, keeper_kho1
):
    """Scope flows in from the selector; the service never restates it."""
    updated = widget_bulk_annotate(actor=keeper_kho1, note="swept")

    annotated = set(
        Widget.objects.filter(notes="swept").values_list("name", flat=True)
    )
    # Write scope is warehouse AND assigned — narrower than what they can read
    # — and the locked row is skipped by the invariant, not by permission.
    assert annotated == {"kho1-assigned"}
    assert updated == 1


def test_bulk_service_touches_nothing_for_an_unprivileged_actor(
    policy, grants, widgets, outsider
):
    assert widget_bulk_annotate(actor=outsider, note="nope") == 0
    assert not Widget.objects.filter(notes="nope").exists()


def test_bulk_service_skips_rows_the_invariant_would_refuse(
    policy, grants, widgets, admin_user
):
    """A sweep skips locked rows and annotates the rest.

    The distinction the design turns on: an invariant is a fact about the row,
    so a bulk job may reasonably step over it.  A *permission* failure is never
    stepped over — see the transfer rollback tests above.
    """
    updated = widget_bulk_annotate(actor=admin_user, note="all")

    annotated = set(Widget.objects.filter(notes="all").values_list("name", flat=True))
    assert annotated == {"kho1-assigned", "kho1-unassigned", "kho2"}
    assert updated == 3
    widgets["kho1_locked"].refresh_from_db()
    assert widgets["kho1_locked"].notes == ""


# -- non-HTTP entrypoint -------------------------------------------------


def test_management_command_enforces_permissions(
    policy, grants, widgets, keeper_kho1, capsys
):
    """No view, no request, no DRF permission class — the guard still applies."""
    call_command(
        "annotate_widgets", username=keeper_kho1.username, note="from-cli"
    )
    assert capsys.readouterr().out.strip() == "annotated 1"

    annotated = set(
        Widget.objects.filter(notes="from-cli").values_list("name", flat=True)
    )
    assert annotated == {"kho1-assigned"}


def test_management_command_grants_nothing_extra_to_an_outsider(
    policy, grants, widgets, outsider, capsys
):
    call_command("annotate_widgets", username=outsider.username, note="cli-nope")
    assert capsys.readouterr().out.strip() == "annotated 0"
    assert not Widget.objects.filter(notes="cli-nope").exists()
