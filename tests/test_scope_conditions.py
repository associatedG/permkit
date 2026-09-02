"""The scope shapes the requirements spec actually asks for.

Derived from the permission matrix: 40% of requirement rows carry a row-scope
condition, and the dominant one ("only the product lines I am responsible
for") is many-to-many, not a scalar match.
"""

from __future__ import annotations

import pytest

from .dummy.models import Crate, Widget
from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


def test_many_to_many_scope(policy, store, widgets, make_user):
    """"Only the lines I am responsible for" — a person may cover several."""
    store.grant_endpoint("sales", "widget.view")
    store.grant_object(
        "sales",
        "widget.view",
        name="sales-own-lines",
        conditions=[
            {"condition": "widget.my_crates"}
        ],
    )
    crate = Crate.objects.get(name="crate-1")
    other = Crate.objects.create(name="crate-2")
    Widget.objects.filter(name="kho2").update(crate=other)

    user = make_user(role="sales")
    user.crates.add(crate)

    visible = set(widget_list(fetched_by=user).values_list("name", flat=True))
    assert visible == {"kho1-assigned", "kho1-unassigned"}

    user.crates.add(other)
    assert "kho2" in set(widget_list(fetched_by=user).values_list("name", flat=True))


def test_empty_collection_yields_no_rows(policy, store, widgets, make_user):
    """Responsible for nothing means seeing nothing — not everything."""
    store.grant_endpoint("sales", "widget.view")
    store.grant_object(
        "sales",
        "widget.view",
        name="sales-own-lines",
        conditions=[
            {"condition": "widget.my_crates"}
        ],
    )
    assert widget_list(fetched_by=make_user(role="sales")).count() == 0


def test_multi_valued_condition_deduplicates_rows(policy, store, widgets, make_user):
    """A to-many traversal must not return the same row twice.

    Without ``.distinct()`` a widget watched by three same-warehouse users
    would appear three times — corrupting counts and pagination.
    """
    store.grant_endpoint("watcher", "widget.view")
    store.grant_object(
        "watcher",
        "widget.view",
        name="watcher-same-warehouse",
        conditions=[
            {"condition": "widget.watched_by_my_warehouse"}
        ],
    )
    widget = widgets["kho1_assigned"]
    for _ in range(3):
        widget.watchers.add(make_user(role="observer", warehouse="KHO_1"))

    user = make_user(role="watcher", warehouse="KHO_1")
    rows = list(widget_list(fetched_by=user))

    assert len(rows) == 1, "row must not be duplicated by the to-many join"
    assert rows[0].pk == widget.pk


def test_role_dependent_state_window_is_a_scope_not_an_invariant(
    policy, store, widgets, make_user
):
    """The same button, different state windows per role.

    The spec gives sales cancel rights *before* handover and coordination
    *after* — so status is a role-dependent condition here, expressed as an
    object grant.  Invariants remain for rules that bind everyone.
    """
    for role, statuses in (
        ("early_stage", ["DRAFT"]),
        ("late_stage", ["ACTIVE", "LOCKED"]),
    ):
        store.grant_endpoint(role, "widget.view")
        store.grant_object(
            role,
            "widget.view",
            name=f"{role}-window",
            conditions=[
                {"condition": "widget.status_in", "params": {"values": statuses}}
            ],
        )

    early = set(
        widget_list(fetched_by=make_user(role="early_stage")).values_list(
            "status", flat=True
        )
    )
    late = set(
        widget_list(fetched_by=make_user(role="late_stage")).values_list(
            "status", flat=True
        )
    )

    assert early == {"DRAFT"}
    assert late == {"LOCKED"}
    assert not (early & late), "the two windows must not overlap"
