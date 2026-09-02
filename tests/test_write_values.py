"""Write tier: which *values* a field may take, and which rows an FK may name.

The endpoint/object/field tiers answer "which rows" and "which fields".  They
do not, on their own, stop a caller writing a legal field to an illegal value —
most importantly a foreign key pointing at a row they could never see.  The
picker is filtered client-side; the API has to be filtered too.
"""

from __future__ import annotations

import pytest

from permkit import PermissionDenied, assert_writable

from .dummy.models import Crate, Widget
from .dummy.services import widget_create, widget_update

pytestmark = pytest.mark.django_db


@pytest.fixture
def crates(db):
    mine, _ = Crate.objects.get_or_create(name="crate-1")
    theirs, _ = Crate.objects.get_or_create(name="crate-2")
    return {"mine": mine, "theirs": theirs}


@pytest.fixture
def scoped_writer(store, make_user, crates):
    """A role that may write, but only sees ``crate-1``."""
    store.grant_endpoint("scoped", "widget.update")
    store.grant_endpoint("scoped", "widget.create")
    store.grant_endpoint("scoped", "crate.view")
    store.grant_object("scoped", "widget.update", name="scoped-widgets")
    store.grant_object(
        "scoped",
        "crate.view",
        name="scoped-crates",
        conditions=[
            {"block": "crate.named", "params": {"names": ["crate-1"]}}
        ],
    )
    store.grant_field(
        "scoped",
        "widget.update",
        name="scoped-write",
        allowed_fields=["notes", "status", "crate"],
        allowed_values={"status": ["DRAFT", "ACTIVE"]},
    )
    store.grant_field(
        "scoped",
        "widget.create",
        name="scoped-create",
        allowed_fields=["notes", "status", "crate"],
        allowed_values={"status": ["DRAFT"]},
    )
    return make_user(role="scoped")


# -- value constraints ---------------------------------------------------


def test_permitted_value_is_accepted(policy, widgets, scoped_writer):
    widget_update(actor=scoped_writer, widget=widgets["kho1_assigned"], status="ACTIVE")
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].status == "ACTIVE"


def test_forbidden_value_is_rejected(policy, widgets, scoped_writer):
    """The field is writable; this particular value is not."""
    with pytest.raises(PermissionDenied, match="status"):
        widget_update(
            actor=scoped_writer, widget=widgets["kho1_assigned"], status="LOCKED"
        )
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].status == "DRAFT"


def test_value_constraints_differ_per_key(policy, widgets, scoped_writer):
    """``widget.create`` permits only DRAFT; ``widget.update`` also ACTIVE."""
    with pytest.raises(PermissionDenied, match="status"):
        widget_create(actor=scoped_writer, name="w", status="ACTIVE")

    widget_create(actor=scoped_writer, name="w", status="DRAFT")
    assert Widget.objects.filter(name="w").exists()


def test_unconstrained_field_accepts_any_value(policy, widgets, scoped_writer):
    """Only fields named in ``allowed_values`` are value-constrained."""
    widget_update(actor=scoped_writer, widget=widgets["kho1_assigned"], notes="anything")
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].notes == "anything"


def test_a_permissive_grant_does_not_lift_a_constraint(
    policy, store, widgets, scoped_writer
):
    """Silence is not permission.

    A second grant that permits ``status`` without constraining its values
    contributes no values — otherwise adding a broad grant would quietly
    erase a narrow one.
    """
    store.grant_field(
        "scoped",
        "widget.update",
        name="scoped-write-extra",
        allowed_fields=["status"],  # no allowed_values
    )
    with pytest.raises(PermissionDenied, match="status"):
        widget_update(
            actor=scoped_writer, widget=widgets["kho1_assigned"], status="LOCKED"
        )


def test_constraints_union_across_grants(policy, store, widgets, scoped_writer):
    """Two grants that *do* constrain the field union their permitted values."""
    store.grant_field(
        "scoped",
        "widget.update",
        name="scoped-write-locked",
        allowed_fields=["status"],
        allowed_values={"status": ["LOCKED"]},
    )
    widget_update(actor=scoped_writer, widget=widgets["kho1_assigned"], status="LOCKED")
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].status == "LOCKED"


# -- reference scope -----------------------------------------------------


def test_foreign_key_within_scope_is_accepted(
    policy, widgets, crates, scoped_writer
):
    widget_update(
        actor=scoped_writer, widget=widgets["kho1_assigned"], crate=crates["mine"]
    )
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].crate == crates["mine"]


def test_foreign_key_outside_scope_is_rejected(
    policy, widgets, crates, scoped_writer
):
    """The gap this closes: a legal field pointed at an unreachable row.

    ``scoped_writer`` may write ``crate`` and can see only ``crate-1``.
    Nothing in the field tier alone stops them naming ``crate-2``.
    """
    with pytest.raises(PermissionDenied, match="outside your scope"):
        widget_update(
            actor=scoped_writer, widget=widgets["kho1_assigned"], crate=crates["theirs"]
        )
    widgets["kho1_assigned"].refresh_from_db()
    assert widgets["kho1_assigned"].crate != crates["theirs"]


def test_foreign_key_accepts_a_primary_key_not_only_an_instance(
    policy, crates, scoped_writer
):
    """Payloads carry ids as often as instances; both must be checked."""
    assert_writable(
        {"crate": crates["mine"].pk}, user=scoped_writer, key="widget.create"
    )
    with pytest.raises(PermissionDenied, match="outside your scope"):
        assert_writable(
            {"crate": crates["theirs"].pk}, user=scoped_writer, key="widget.create"
        )


def test_reference_scope_applies_on_create(policy, crates, scoped_writer):
    widget_create(actor=scoped_writer, name="ok", crate=crates["mine"])
    assert Widget.objects.filter(name="ok").exists()

    with pytest.raises(PermissionDenied, match="outside your scope"):
        widget_create(actor=scoped_writer, name="bad", crate=crates["theirs"])
    assert not Widget.objects.filter(name="bad").exists()


def test_nonexistent_reference_is_rejected(policy, scoped_writer):
    with pytest.raises(PermissionDenied, match="outside your scope"):
        assert_writable({"crate": 999999}, user=scoped_writer, key="widget.create")


def test_null_reference_is_allowed(policy, scoped_writer):
    """Clearing an optional FK is not a scope violation."""
    assert_writable({"crate": None}, user=scoped_writer, key="widget.create")


def test_role_without_read_scope_on_the_target_cannot_reference_it(
    policy, store, crates, make_user
):
    """Reference scope is the target key's object grants, not a new rule.

    A role with no ``crate.view`` grant at all can name no crate.
    """
    store.grant_endpoint("blind", "widget.create")
    store.grant_field(
        "blind", "widget.create", name="blind-create", allowed_fields=["crate"]
    )
    blind = make_user(role="blind")

    with pytest.raises(PermissionDenied, match="outside your scope"):
        assert_writable({"crate": crates["mine"].pk}, user=blind, key="widget.create")


def test_assert_writable_denies_without_the_endpoint_grant(policy, store, make_user):
    """Defence in depth for a service that forgot its ``@requires``.

    ``assert_writable`` judges only *controlled* fields, so a payload made of
    ordinary ones — a name, a warehouse — would otherwise pass completely and
    an undecorated service would create rows for a principal with no grants.
    """
    actor = make_user(role="nobody")
    with pytest.raises(PermissionDenied):
        assert_writable({"name": "trojan"}, user=actor, key="widget.create")


def test_a_payload_of_uncontrolled_fields_is_still_gated(
    policy, store, make_user
):
    """The endpoint grant is what admits the caller; fields are then filtered."""
    store.grant_endpoint("plain", "widget.create")
    actor = make_user(role="plain")

    # Admitted, and ``name`` is not permission-controlled, so it passes.
    assert_writable({"name": "fine"}, user=actor, key="widget.create")

    # ... but a controlled field still needs its own grant.
    with pytest.raises(PermissionDenied, match="secret_price"):
        assert_writable({"secret_price": 1}, user=actor, key="widget.create")
