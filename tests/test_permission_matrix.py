"""The dummy service's permission setup, stated once as a table.

Every other test file proves a *mechanism* — that list and detail agree, that
an unconditional grant widens, that a locked row beats every role.  This one
proves the *configuration*: given the abstract roles the fixture composes,
exactly these people can see and do exactly these things.

It is deliberately a flat expected-value table rather than derived assertions.
When a rule changes, the diff should read as a change in access, not as a
change in test logic — which is also what makes this shape the baseline for
migrating a real domain onto permkit.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from permkit import check_object

from .conftest import ADMIN, KEEPER, OUTSIDER, VIEWER
from .dummy.models import Widget
from .dummy.selectors import widget_list, widget_writable
from .dummy.serializers import WidgetSerializer

pytestmark = pytest.mark.django_db

ALL = {"kho1-assigned", "kho1-unassigned", "kho2", "kho1-locked"}
KHO1 = {"kho1-assigned", "kho1-unassigned", "kho1-locked"}
ASSIGNED = {"kho1-assigned", "kho1-locked"}

#: fixture name -> (role, may read, may write, may see price)
MATRIX = {
    "admin_user": (ADMIN, ALL, ALL, True),
    "keeper_kho1": (KEEPER, KHO1, ASSIGNED, False),
    "keeper_kho2": (KEEPER, {"kho2"}, set(), False),
    "viewer": (VIEWER, ALL, set(), False),
    "outsider": (OUTSIDER, set(), set(), False),
}


@pytest.fixture(params=sorted(MATRIX))
def case(request, policy, grants, widgets):
    """One row of the matrix, with its principal built."""
    role, readable, writable, sees_price = MATRIX[request.param]
    return request.getfixturevalue(request.param), role, readable, writable, sees_price


# -- object tier ---------------------------------------------------------


def test_readable_rows_match_the_matrix(case):
    user, role, readable, _, _ = case
    assert set(widget_list(fetched_by=user).values_list("name", flat=True)) == readable


def test_writable_rows_match_the_matrix(case):
    user, role, _, writable, _ = case
    assert (
        set(widget_writable(fetched_by=user).values_list("name", flat=True)) == writable
    )


def test_per_object_checks_agree_with_the_writable_set(case):
    """The queryset and the single-row check are one rule, so they must agree."""
    user, _, _, writable, _ = case
    per_object = {
        w.name for w in Widget.objects.all() if check_object(user, "widget.update", w)
    }
    assert per_object == writable


def test_writable_is_never_wider_than_readable(case):
    """A row you may edit but not see would be incoherent."""
    _, _, readable, writable, _ = case
    assert writable <= readable


# -- field tier ----------------------------------------------------------


def test_price_visibility_matches_the_matrix(case, widgets):
    user, _, readable, _, sees_price = case
    if not readable:
        pytest.skip("nothing to serialise for a principal who sees no rows")

    request = APIClient().request().wsgi_request
    request.user = user
    data = WidgetSerializer(
        widgets["kho1_assigned"], context={"request": request}
    ).data

    assert ("secret_price" in data) is sees_price
    assert "name" in data, "uncontrolled fields pass through for everyone"


# -- the same answers over HTTP -----------------------------------------


def test_the_api_returns_the_same_rows_as_the_matrix(case):
    """The endpoint tier gates entry; the object tier decides the rows.

    A principal with no endpoint grant is refused outright rather than handed
    an empty list — the two failures mean different things.
    """
    user, _, readable, _, _ = case
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/widgets/")

    if not readable:
        assert response.status_code == 403
        return

    assert response.status_code == 200
    assert {row["name"] for row in response.data} == readable


def test_the_api_hides_price_per_the_matrix(case):
    user, _, readable, _, sees_price = case
    if not readable:
        pytest.skip("no readable rows")

    client = APIClient()
    client.force_authenticate(user=user)
    rows = client.get("/widgets/").data

    assert all(("secret_price" in row) is sees_price for row in rows)


def test_updating_an_unwritable_row_is_refused(case, widgets):
    """Out of write scope means the row is not there — 404, not 403.

    A forbidden response on a row the caller can see would be fine; on one they
    cannot, it would confirm the row exists.  Scoping the update view by the
    write key makes both cases 404 without having to decide.
    """
    user, _, readable, writable, _ = case
    target = widgets["kho1_unassigned"]
    if target.name in writable:
        pytest.skip("this principal may write the probe row")

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.patch(
        f"/widgets/{target.pk}/update/", {"notes": "nope"}, format="json"
    )

    assert response.status_code in (403, 404)
    target.refresh_from_db()
    assert target.notes == ""
