"""The last unguarded edge: a role string that matches nothing.

A user's role reaches permkit as text, matched by name. That is what keeps
permkit out of your user model, and it is the one place a typo is silent — the
user is granted nothing and looks exactly like a user deliberately given
nothing. Every other tier already refuses to fail quietly; this is that idea
applied here.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from permkit.catalogue.sync import sync_catalogue
from permkit.models import Permission, Role, RolePermission
from permkit.spec import apply_spec

from .dummy import permissions as dummy_spec

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(db):
    sync_catalogue()
    apply_spec(dummy_spec)


def test_a_role_nobody_defined_fails(seeded, make_user):
    """The whole point: silent-deny becomes loud."""
    make_user(role="w_keper")  # typo

    with pytest.raises(CommandError, match="role problem"):
        call_command("permkit_roles", verbosity=0)


def test_the_report_names_the_typo_and_the_headcount(seeded, make_user, capsys):
    make_user(role="w_keper")
    make_user(role="w_keper")

    with pytest.raises(CommandError):
        call_command("permkit_roles")

    out = capsys.readouterr().out
    assert "'w_keper' is held by 2 user(s)" in out


def test_correct_roles_pass(seeded, make_user):
    make_user(role="w_keeper")
    make_user(role="w_admin")

    call_command("permkit_roles", verbosity=0)


def test_case_and_whitespace_are_not_reported_as_typos(seeded, make_user):
    """The resolver normalises before matching, so the report must too."""
    make_user(role="  W_Keeper  ")

    call_command("permkit_roles", verbosity=0)


def test_a_user_with_no_role_is_reported_but_does_not_fail(seeded, make_user, capsys):
    """Denied everything, which is often exactly what was intended."""
    make_user(role="")

    call_command("permkit_roles")

    assert "no role at all" in capsys.readouterr().out


def test_a_role_nobody_holds_is_a_warning_not_a_failure(seeded, capsys):
    Role.objects.create(key="w_ghost", label="Ghost")

    call_command("permkit_roles")

    assert "nobody holds it" in capsys.readouterr().out


def test_strict_promotes_the_warnings(seeded):
    Role.objects.create(key="w_ghost", label="Ghost")

    with pytest.raises(CommandError):
        call_command("permkit_roles", "--strict", verbosity=0)


def test_a_role_granting_nothing_is_reported(seeded, make_user, capsys):
    """It exists, someone holds it, and it does nothing — worth saying."""
    Role.objects.create(key="w_hollow", label="Hollow")
    make_user(role="w_hollow")

    call_command("permkit_roles")

    assert "holds no permissions" in capsys.readouterr().out


def test_it_works_with_a_resolver_that_is_not_a_plain_column(seeded, make_user, capsys):
    """Any resolver at all — the fallback asks it user by user."""
    from permkit.conf import get_policy

    class ClaimResolver:
        def roles_for(self, user):
            return ["w_admin"] if user.is_superuser else ["w_invented"]

    policy = get_policy()
    original = policy.principals
    policy.principals = ClaimResolver()
    make_user(role="ignored")
    try:
        with pytest.raises(CommandError):
            call_command("permkit_roles")
        out = capsys.readouterr().out
        assert "does not read a plain column" in out
        assert "'w_invented' is held by" in out
    finally:
        policy.principals = original


def test_counts_reflect_reality(seeded, make_user, capsys):
    make_user(role="w_keeper")
    make_user(role="w_keeper")
    make_user(role="w_admin")

    call_command("permkit_roles")

    out = capsys.readouterr().out
    assert "w_keeper" in out and "2 user(s)" in out
    assert Permission.objects.count() == len(dummy_spec.PERMISSIONS)
    assert RolePermission.objects.exists()
