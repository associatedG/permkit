"""End-to-end over HTTP, through routed views.

The unit tests prove the resolver; these prove the DRF adapters are wired to
it — and, critically, that the HTTP path and the selector path agree.  A
library whose mixins quietly diverge from its functions would be worse than no
library at all.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from .dummy.models import Widget
from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def _list(client, user):
    client.force_authenticate(user=user)
    return client.get("/widgets/")


def test_list_endpoint_matches_the_selector(
    policy, grants, widgets, keeper_kho1, keeper_kho2, viewer, admin_user
):
    """The DRF mixin and the selector must return the same rows.

    Same ``Q``, two call paths — if these ever disagree, the object tier has
    two sources of truth again.
    """
    client = APIClient()
    for user in (admin_user, keeper_kho1, keeper_kho2, viewer):
        client.force_authenticate(user=user)
        response = client.get("/widgets/")

        assert response.status_code == 200
        via_http = {row["name"] for row in response.data}
        via_selector = set(widget_list(fetched_by=user).values_list("name", flat=True))
        assert via_http == via_selector, f"divergence for {user.role}"


def test_role_without_endpoint_grant_gets_403(policy, grants, widgets, outsider):
    response = _list(APIClient(), outsider)
    assert response.status_code == 403


def test_out_of_scope_row_is_404_not_403_on_read(
    policy, grants, widgets, keeper_kho1
):
    """404 rather than 403: a forbidden response would confirm the row exists."""
    client = APIClient()
    client.force_authenticate(user=keeper_kho1)

    assert client.get(f"/widgets/{widgets['kho1_assigned'].pk}/").status_code == 200
    assert client.get(f"/widgets/{widgets['kho2'].pk}/").status_code == 404


def test_write_scope_is_narrower_than_read_scope_over_http(
    policy, grants, widgets, keeper_kho1
):
    """The keeper can *read* an unassigned row but not reach it to update."""
    client = APIClient()
    client.force_authenticate(user=keeper_kho1)
    unassigned = widgets["kho1_unassigned"]

    assert client.get(f"/widgets/{unassigned.pk}/").status_code == 200
    response = client.patch(
        f"/widgets/{unassigned.pk}/update/", {"notes": "nope"}, format="json"
    )
    assert response.status_code == 404

    unassigned.refresh_from_db()
    assert unassigned.notes == ""


def test_writable_field_succeeds_over_http(policy, grants, widgets, keeper_kho1):
    client = APIClient()
    client.force_authenticate(user=keeper_kho1)
    widget = widgets["kho1_assigned"]

    response = client.patch(
        f"/widgets/{widget.pk}/update/", {"notes": "stocktake"}, format="json"
    )
    assert response.status_code == 200

    widget.refresh_from_db()
    assert widget.notes == "stocktake"


def test_forbidden_field_is_403_not_500(policy, grants, widgets, keeper_kho1):
    """Exercises the exception handler: a Layer 1 denial must become a 403."""
    client = APIClient()
    client.force_authenticate(user=keeper_kho1)
    widget = widgets["kho1_assigned"]

    response = client.patch(
        f"/widgets/{widget.pk}/update/", {"secret_price": "9.99"}, format="json"
    )
    assert response.status_code == 403

    widget.refresh_from_db()
    assert widget.secret_price == 100


def test_response_body_never_leaks_a_withheld_field(
    policy, grants, widgets, keeper_kho1
):
    """Including on the response to a successful write."""
    client = APIClient()
    client.force_authenticate(user=keeper_kho1)
    widget = widgets["kho1_assigned"]

    listed = client.get("/widgets/")
    for row in listed.data:
        assert "secret_price" not in row

    patched = client.patch(
        f"/widgets/{widget.pk}/update/", {"notes": "x"}, format="json"
    )
    assert patched.status_code == 200
    assert "secret_price" not in patched.data


def test_admin_sees_every_row_and_the_price(policy, grants, widgets, admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    response = client.get("/widgets/")

    assert response.status_code == 200
    assert len(response.data) == Widget.objects.count()
    assert all("secret_price" in row for row in response.data)
