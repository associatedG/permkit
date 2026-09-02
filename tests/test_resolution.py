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
    ``DatabaseStore``.  If they diverge, the suite proves nothing.
    """
    from permkit.conf import reset_policy, set_policy

    memory_visible = set(widget_list(fetched_by=keeper_kho1).values_list("pk", flat=True))
    assert memory_visible, "fixture should grant the keeper some rows"

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
