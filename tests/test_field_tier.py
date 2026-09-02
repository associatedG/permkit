"""Field tier: read and write resolved from separate keys, including nesting."""

from __future__ import annotations

import pytest
from rest_framework.test import APIRequestFactory

from permkit import PermissionDenied, strip_fields

from .dummy.models import Crate
from .dummy.serializers import CrateSerializer, WidgetSerializer
from .dummy.services import widget_update

pytestmark = pytest.mark.django_db


def _ctx(user):
    request = APIRequestFactory().get("/")
    request.user = user
    return {"request": request}


def test_controlled_field_is_stripped(policy, grants, widgets, keeper_kho1):
    data = WidgetSerializer(widgets["kho1_assigned"], context=_ctx(keeper_kho1)).data
    assert "secret_price" not in data
    assert data["name"] == "kho1-assigned"


def test_uncontrolled_fields_pass_through(policy, grants, widgets, keeper_kho1):
    """Adopting the field tier costs one declaration per *sensitive* field.

    Anything absent from the key's ``fields`` is unrestricted, so a project
    does not have to enumerate every column to switch the tier on.
    """
    data = WidgetSerializer(widgets["kho1_assigned"], context=_ctx(keeper_kho1)).data
    for uncontrolled in ("id", "name", "status", "warehouse", "notes"):
        assert uncontrolled in data


def test_field_is_stripped_through_a_nested_serializer(
    policy, grants, widgets, keeper_kho1
):
    """A field tier that only guards the outermost object is a leak."""
    crate = Crate.objects.get(name="crate-1")
    data = CrateSerializer(crate, context=_ctx(keeper_kho1)).data

    assert data["widgets"], "fixture should nest widgets"
    for nested in data["widgets"]:
        assert "secret_price" not in nested
        assert "name" in nested


def test_grants_union_so_an_extra_grant_only_reveals_more(
    policy, store, grants, widgets, make_user
):
    """Allow-lists union. Holding another grant can never *remove* a field."""
    store.grant_endpoint("dual", "widget.view")
    store.grant_object("dual", "widget.view", name="dual-all")
    store.grant_field(
        "dual", "widget.view", name="dual-nothing", allowed_fields=[]
    )
    user = make_user(role="dual")
    assert "secret_price" not in WidgetSerializer(
        widgets["kho2"], context=_ctx(user)
    ).data

    store.grant_field(
        "dual", "widget.view", name="dual-price", allowed_fields=["secret_price"]
    )
    assert "secret_price" in WidgetSerializer(widgets["kho2"], context=_ctx(user)).data


def test_one_update_resolves_read_and_write_from_different_keys(
    policy, grants, widgets, keeper_kho1
):
    """The payoff of separating read and write.

    The keeper may *write* ``notes`` but may never *read* ``secret_price``.
    Both resolve during a single update, from ``widget.update`` and
    ``widget.view`` respectively — no special case required.
    """
    widget = widgets["kho1_assigned"]

    widget_update(actor=keeper_kho1, widget=widget, notes="checked in")
    widget.refresh_from_db()
    assert widget.notes == "checked in"

    with pytest.raises(PermissionDenied):
        widget_update(actor=keeper_kho1, widget=widget, secret_price=1)

    assert "secret_price" not in WidgetSerializer(widget, context=_ctx(keeper_kho1)).data


def test_write_permission_does_not_imply_read_permission(
    policy, grants, widgets, keeper_kho1
):
    """``notes`` is writable for the keeper; that must not grant reading price."""
    assert "notes" in policy.visible_fields(
        keeper_kho1, "widget.update", ["notes", "secret_price"]
    )
    assert "secret_price" not in policy.visible_fields(
        keeper_kho1, "widget.update", ["notes", "secret_price"]
    )
    assert "secret_price" not in policy.visible_fields(
        keeper_kho1, "widget.view", ["secret_price"]
    )


def test_strip_fields_works_without_a_serializer(policy, grants, keeper_kho1):
    """Layer 1 needs no DRF — the same rule applies to a plain dict."""
    payload = {"name": "x", "secret_price": 10}
    assert strip_fields(payload, user=keeper_kho1, key="widget.view") == {"name": "x"}
