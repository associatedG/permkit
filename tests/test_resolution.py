"""Role normalisation, store parity, and the explain trace."""

from __future__ import annotations

import pytest

from permkit.principals import AttributeRoleResolver
from permkit.resolver import Policy
from permkit.store import DatabaseStore, seed_database_from

from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("stored_role", ["w_keeper", "W_KEEPER", "  W_Keeper  "])
def test_role_matching_is_case_and_whitespace_insensitive(
    policy, grants, widgets, make_user, stored_role
):
    """Role strings arrive inconsistently cased from real systems.

    Comparing them raw is how a permission check silently fails, so
    normalisation happens once, centrally.
    """
    user = make_user(role=stored_role, warehouse="KHO_1")
    assert widget_list(fetched_by=user).count() == 3


def test_database_store_matches_memory_store(policy, grants, widgets, keeper_kho1):
    """The two stores must be interchangeable.

    Tests are written against ``MemoryStore``; production runs on
    ``DatabaseStore``, which resolves through the composed permission tables.
    If they diverge, the suite proves nothing.

    This is also the parity check the composition tier was built against: the
    keeper's two hand-written grants become a permission holding two rules,
    and the rows they admit must be identical.
    """
    from permkit.catalogue.sync import sync_catalogue
    from permkit.conf import reset_policy, set_policy

    memory_visible = set(widget_list(fetched_by=keeper_kho1).values_list("pk", flat=True))
    assert memory_visible, "fixture should grant the keeper some rows"

    # Composition points at the catalogue by foreign key, so there is nothing
    # to compose against until the declarations have been published.
    sync_catalogue()
    seed_database_from(grants)
    set_policy(Policy(store=DatabaseStore(), principals=AttributeRoleResolver("role")))
    try:
        db_visible = set(
            widget_list(fetched_by=keeper_kho1).values_list("pk", flat=True)
        )
    finally:
        reset_policy()

    assert db_visible == memory_visible


def test_explain_reports_why_a_row_is_out_of_scope(
    policy, grants, widgets, keeper_kho1
):
    """Once rules live in data, the trace is how anyone answers "why not?"."""
    trace = policy.explain(keeper_kho1, "widget.update", widgets["kho1_unassigned"])
    rendered = str(trace)

    assert "w_keeper" in rendered
    assert "OUT of scope" in rendered
    assert "widget.assigned" in rendered


def test_explain_reports_withheld_fields(policy, grants, widgets, keeper_kho1):
    trace = policy.explain(keeper_kho1, "widget.view")
    assert "secret_price" in str(trace)


def test_explain_reports_a_principal_with_no_roles(policy, grants, make_user):
    trace = policy.explain(make_user(role=""), "widget.view")
    assert trace.allowed is False
    assert "no roles" in str(trace)


def test_explain_verdict_accounts_for_the_row_when_one_is_named(
    policy, grants, widgets, keeper_kho1
):
    """"May they update?" and "may they update *this*?" are different questions.

    The keeper holds the endpoint grant, so the endpoint tier says yes. The
    row is not assigned to them, so the object tier says no. A preview screen
    that reported the first would tell an administrator "allowed" about a row
    the same policy refuses.
    """
    denied = policy.explain(
        keeper_kho1, "widget.update", widgets["kho1_unassigned"]
    )
    assert denied.allowed is False

    permitted = policy.explain(
        keeper_kho1, "widget.update", widgets["kho1_assigned"]
    )
    assert permitted.allowed is True

    # With no row named, the verdict is still the endpoint tier's alone.
    assert policy.explain(keeper_kho1, "widget.update").allowed is True
