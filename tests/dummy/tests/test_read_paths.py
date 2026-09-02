"""R1-R4 — every read the dummy service exposes.

One happy path per route, then the ways each one is refused. See
``PERMISSIONS.md`` for the tier each check belongs to.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

ALL = {"kho1-assigned", "kho1-unassigned", "kho2", "kho1-locked"}
KHO1 = {"kho1-assigned", "kho1-unassigned", "kho1-locked"}
ASSIGNED = {"kho1-assigned", "kho1-locked"}


def _names(response) -> set[str]:
    return {row["name"] for row in response.data}


# -- R1  GET /widgets/ ----------------------------------------------------


def test_R1_happy_admin_lists_every_widget(policy, grants, widgets, admin_user, api):
    response = api.as_(admin_user).get("/widgets/")

    assert response.status_code == 200
    assert _names(response) == ALL


def test_R1_denied_no_endpoint_grant_is_403(policy, grants, widgets, outsider, api):
    """A role with no grants is refused outright, not handed an empty list."""
    response = api.as_(outsider).get("/widgets/")

    assert response.status_code == 403


def test_R1_denied_object_tier_narrows_silently(
    policy, grants, widgets, keeper_kho1, keeper_kho2, api
):
    """Holding the endpoint grant, rows outside the scope are simply absent.

    This is not an error: the keeper legitimately may list widgets, and these
    are the widgets there are for them.
    """
    assert _names(api.as_(keeper_kho1).get("/widgets/")) == KHO1
    assert _names(api.as_(keeper_kho2).get("/widgets/")) == {"kho2"}


def test_R1_denied_field_tier_strips_the_price(
    policy, grants, widgets, admin_user, keeper_kho1, viewer, api
):
    """``secret_price`` is the only controlled field; the rest pass through."""
    admin_row = api.as_(admin_user).get("/widgets/").data[0]
    assert "secret_price" in admin_row

    for user in (keeper_kho1, viewer):
        row = api.as_(user).get("/widgets/").data[0]
        assert "secret_price" not in row, f"{user.role} must not see the price"
        assert "name" in row, "uncontrolled fields still render"


# -- R2  GET /widgets/<pk>/ ----------------------------------------------


def test_R2_happy_keeper_reads_a_row_in_scope(
    policy, grants, widgets, keeper_kho1, api
):
    widget = widgets["kho1_assigned"]
    response = api.as_(keeper_kho1).get(f"/widgets/{widget.pk}/")

    assert response.status_code == 200
    assert response.data["name"] == "kho1-assigned"


def test_R2_denied_out_of_scope_row_is_404_not_403(
    policy, grants, widgets, keeper_kho1, api
):
    """404 on purpose: a 403 would confirm the row exists."""
    response = api.as_(keeper_kho1).get(f"/widgets/{widgets['kho2'].pk}/")

    assert response.status_code == 404


def test_R2_denied_out_of_scope_is_indistinguishable_from_absent(
    policy, grants, widgets, keeper_kho1, api
):
    """The id-probing defence: both cases must look identical."""
    hidden = api.as_(keeper_kho1).get(f"/widgets/{widgets['kho2'].pk}/")
    missing = api.as_(keeper_kho1).get("/widgets/99999/")

    assert hidden.status_code == missing.status_code == 404


def test_R2_denied_no_endpoint_grant_is_403(
    policy, grants, widgets, outsider, api
):
    response = api.as_(outsider).get(f"/widgets/{widgets['kho2'].pk}/")

    assert response.status_code == 403


# -- R3  GET /crates/ -----------------------------------------------------


def test_R3_happy_admin_sees_every_crate(
    policy, grants, widgets, admin_user, far_crate, api
):
    response = api.as_(admin_user).get("/crates/")

    assert response.status_code == 200
    assert {row["name"] for row in response.data} == {"crate-1", "far-crate"}


def test_R3_denied_restricted_role_sees_only_its_crate(
    policy, widgets, filer, far_crate, api
):
    """The picker returns exactly what W3 will later accept."""
    response = api.as_(filer).get("/crates/")

    assert response.status_code == 200
    assert {row["name"] for row in response.data} == {"crate-1"}


def test_R3_denied_no_crate_grant_is_403(policy, grants, widgets, viewer, api):
    """The viewer may read widgets but was never granted crates."""
    response = api.as_(viewer).get("/crates/")

    assert response.status_code == 403


# -- R4  GET /widgets/writable/ ------------------------------------------


def test_R4_happy_writable_is_narrower_than_readable(
    policy, grants, widgets, keeper_kho1, api
):
    """The point of a separate key: the two lists legitimately differ."""
    readable = _names(api.as_(keeper_kho1).get("/widgets/"))
    writable = _names(api.as_(keeper_kho1).get("/widgets/writable/"))

    assert readable == KHO1
    assert writable == ASSIGNED
    assert writable < readable, "write scope must be the narrower set"


def test_R4_denied_read_only_role_gets_403(policy, grants, widgets, viewer, api):
    """The viewer reads every widget and holds no ``widget.update`` grant."""
    assert api.as_(viewer).get("/widgets/").status_code == 200
    assert api.as_(viewer).get("/widgets/writable/").status_code == 403
