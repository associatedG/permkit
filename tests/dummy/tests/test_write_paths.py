"""W1-W3 and N1 — every write the dummy service exposes.

One happy path per route, then each way it is refused, in the order the
checks actually run. See ``PERMISSIONS.md``.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from django.core.management import call_command

from ..models import Widget

pytestmark = pytest.mark.django_db


# -- W1  POST /widgets/create/ -------------------------------------------


def test_W1_happy_admin_creates_a_widget(policy, grants, widgets, admin_user, api):
    response = api.as_(admin_user).post(
        "/widgets/create/", {"name": "fresh", "notes": "hello"}, format="json"
    )

    assert response.status_code == 201, response.data
    assert Widget.objects.filter(name="fresh", owner=admin_user).exists()


def test_W1_denied_without_the_create_grant_is_403(
    policy, grants, widgets, keeper_kho1, api
):
    """``widget.update`` does not carry over to ``widget.create``.

    The keeper may edit rows and still may not make them.
    """
    response = api.as_(keeper_kho1).post(
        "/widgets/create/", {"name": "nope"}, format="json"
    )

    assert response.status_code == 403
    assert not Widget.objects.filter(name="nope").exists()


def test_W1_happy_field_grants_are_per_key(
    policy, grants, widgets, admin_user, api
):
    """The create key's field grant lists ``notes``, not ``status``.

    ``secret_price`` is granted on create, so it is accepted; the separate
    grant is what makes that a deliberate decision rather than a spillover
    from ``widget.update``.
    """
    response = api.as_(admin_user).post(
        "/widgets/create/",
        {"name": "priced", "secret_price": "9.99"},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Widget.objects.get(name="priced").secret_price == Decimal("9.99")


def test_W1_denied_no_grants_at_all_is_403(policy, grants, widgets, outsider, api):
    response = api.as_(outsider).post(
        "/widgets/create/", {"name": "nope"}, format="json"
    )

    assert response.status_code == 403


# -- W2  PATCH /widgets/<pk>/update/ -------------------------------------


def test_W2_happy_keeper_updates_an_assigned_row(
    policy, grants, widgets, keeper_kho1, api
):
    widget = widgets["kho1_assigned"]
    response = api.as_(keeper_kho1).patch(
        f"/widgets/{widget.pk}/update/", {"notes": "touched"}, format="json"
    )

    assert response.status_code == 200, response.data
    widget.refresh_from_db()
    assert widget.notes == "touched"


def test_W2_denied_step1_no_endpoint_grant_is_403(
    policy, grants, widgets, viewer, api
):
    """The viewer reads every row and may write none."""
    widget = widgets["kho1_assigned"]
    response = api.as_(viewer).patch(
        f"/widgets/{widget.pk}/update/", {"notes": "nope"}, format="json"
    )

    assert response.status_code == 403
    widget.refresh_from_db()
    assert widget.notes == ""


def test_W2_denied_step2_readable_but_not_writable_is_404(
    policy, grants, widgets, keeper_kho1, api
):
    """The row is in this keeper's warehouse but is not assigned to them.

    It appears in ``GET /widgets/`` and is a 404 here — the mutation route
    draws from the write scope.
    """
    widget = widgets["kho1_unassigned"]
    assert api.as_(keeper_kho1).get(f"/widgets/{widget.pk}/").status_code == 200

    response = api.as_(keeper_kho1).patch(
        f"/widgets/{widget.pk}/update/", {"notes": "nope"}, format="json"
    )

    assert response.status_code == 404
    widget.refresh_from_db()
    assert widget.notes == ""


def test_W2_denied_step3_field_not_granted_is_403(
    policy, grants, widgets, keeper_kho1, api
):
    """The keeper cannot write the price it cannot read."""
    widget = widgets["kho1_assigned"]
    response = api.as_(keeper_kho1).patch(
        f"/widgets/{widget.pk}/update/", {"secret_price": "1.00"}, format="json"
    )

    assert response.status_code == 403
    widget.refresh_from_db()
    assert widget.secret_price == 100


def test_W2_denied_step5_reference_outside_scope_is_403(
    policy, widgets, filer, far_crate, api
):
    """May edit the widget, may write ``crate`` — but not to *that* crate."""
    widget = widgets["kho2"]
    before = widget.crate

    response = api.as_(filer).patch(
        f"/widgets/{widget.pk}/update/", {"crate": far_crate.pk}, format="json"
    )

    assert response.status_code == 403, response.data
    widget.refresh_from_db()
    assert widget.crate == before, "a denied reference must not write"


def test_W2_denied_step6_locked_row_is_409_for_everyone(
    policy, grants, widgets, admin_user, keeper_kho1, api
):
    """An invariant, not a permission — the admin's blanket grant does not help.

    409 rather than 403 is the whole point: this is not a question about who
    you are, so no grant can be added to make it succeed.
    """
    locked = widgets["kho1_locked"]

    for user in (admin_user, keeper_kho1):
        response = api.as_(user).patch(
            f"/widgets/{locked.pk}/update/", {"notes": "bypass"}, format="json"
        )
        assert response.status_code == 409, f"{user.role}: {response.data}"

    locked.refresh_from_db()
    assert locked.notes == ""


# -- W3  POST /widgets/<pk>/transfer/ ------------------------------------


def test_W3_happy_files_a_widget_into_a_visible_crate(
    policy, widgets, filer, crate_one, api
):
    widget = widgets["kho2"]
    response = api.as_(filer).post(
        f"/widgets/{widget.pk}/transfer/", {"crate": crate_one.pk}, format="json"
    )

    assert response.status_code == 200, response.data
    widget.refresh_from_db()
    assert widget.crate == crate_one


def test_W3_denied_destination_outside_crate_scope_is_403(
    policy, widgets, filer, far_crate, api
):
    """The crate exists and is fetched fine; the reference check refuses it.

    The picker at ``GET /crates/`` never offered this id, so reaching here
    means the caller supplied one they were not given.
    """
    widget = widgets["kho2"]
    assert {row["name"] for row in api.as_(filer).get("/crates/").data} == {"crate-1"}

    response = api.as_(filer).post(
        f"/widgets/{widget.pk}/transfer/", {"crate": far_crate.pk}, format="json"
    )

    assert response.status_code == 403, response.data
    widget.refresh_from_db()
    assert widget.crate != far_crate


def test_W3_denied_widget_not_writable_is_404(
    policy, grants, widgets, keeper_kho1, crate_one, api
):
    widget = widgets["kho1_unassigned"]
    response = api.as_(keeper_kho1).post(
        f"/widgets/{widget.pk}/transfer/", {"crate": crate_one.pk}, format="json"
    )

    assert response.status_code == 404


def test_W3_denied_locked_row_is_409(
    policy, grants, widgets, admin_user, crate_one, api
):
    locked = widgets["kho1_locked"]
    response = api.as_(admin_user).post(
        f"/widgets/{locked.pk}/transfer/", {"crate": crate_one.pk}, format="json"
    )

    assert response.status_code == 409, response.data


# -- N1  manage.py annotate_widgets --------------------------------------


def test_N1_happy_command_annotates_only_writable_rows(
    policy, grants, widgets, keeper_kho1
):
    """No view, no DRF permission class — the same scope still applies."""
    call_command(
        "annotate_widgets", username=keeper_kho1.username, note="swept"
    )

    assert widgets["kho1_assigned"].__class__.objects.get(
        name="kho1-assigned"
    ).notes == "swept"
    # in the read scope but not the write scope
    assert Widget.objects.get(name="kho1-unassigned").notes == ""
    # locked rows are skipped by the sweep, not failed
    assert Widget.objects.get(name="kho1-locked").notes == ""


def test_N1_denied_role_without_grants_annotates_nothing(
    policy, grants, widgets, outsider
):
    call_command("annotate_widgets", username=outsider.username, note="nope")

    assert not Widget.objects.filter(notes="nope").exists()
