"""Permissions as a re-runnable file.

The admin is where permissions are composed. A spec is for the environments
with nobody sitting in front of them — a fresh database, CI, a deploy. So the
properties that matter are the ones that make it safe to run unattended: it
must be idempotent, it must refuse a reference the catalogue cannot honour,
and it must never quietly revoke something a person granted by hand.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from permkit.catalogue.models import RegisteredFilter
from permkit.catalogue.sync import sync_catalogue
from permkit.exceptions import ConfigurationError, InvalidParams
from permkit.models import (
    Permission,
    PermissionEndpoint,
    PermissionFieldGrant,
    PermissionRule,
    PermissionRuleCondition,
    Role,
    RolePermission,
)
from permkit.spec import apply_spec

from .dummy.models import Widget
from .dummy.selectors import widget_list

pytestmark = pytest.mark.django_db


class Spec:
    """A spec module, as an object — apply_spec only reads two attributes."""

    def __init__(self, permissions=None, roles=None):
        self.PERMISSIONS = permissions or {}
        self.ROLES = roles or {}


KEEPER = Spec(
    permissions={
        "widget-browse-own-warehouse": {
            "name": "Browse my warehouse's widgets",
            "endpoints": ["widget.view"],
            "rules": [
                {
                    "key": "widget.view",
                    "label": "in my warehouse",
                    "conditions": [{"filter": "widget.warehouse"}],
                }
            ],
        }
    },
    roles={
        "w_keeper": {
            "label": "Warehouse keeper",
            "permissions": ["widget-browse-own-warehouse"],
        }
    },
)


@pytest.fixture
def catalogue(db):
    return sync_catalogue()


# -- it does what the file says -------------------------------------------


def test_a_spec_composes_a_working_permission(catalogue, make_user, widgets):
    """The end of the whole chain: a file in git narrows a real queryset."""
    apply_spec(KEEPER)

    keeper = make_user(role="w_keeper", warehouse="KHO_1")
    visible = set(widget_list(fetched_by=keeper).values_list("name", flat=True))

    assert visible == {"kho1-assigned", "kho1-unassigned", "kho1-locked"}


def test_it_builds_every_part(catalogue):
    apply_spec(KEEPER)

    permission = Permission.objects.get(key="widget-browse-own-warehouse")
    assert permission.name == "Browse my warehouse's widgets"
    assert [e.endpoint.key for e in permission.endpoints.all()] == ["widget.view"]
    rule = permission.rules.get()
    assert rule.key == "widget.view"
    assert [c.filter.key for c in rule.conditions.all()] == ["widget.warehouse"]
    assert Role.objects.get(key="w_keeper").permissions.count() == 1


def test_re_applying_changes_nothing(catalogue):
    apply_spec(KEEPER)
    report = apply_spec(KEEPER)

    assert not report.changed
    assert report.unchanged == ["widget-browse-own-warehouse"]
    assert PermissionRuleCondition.objects.count() == 1


# -- the spec is authoritative for what it names --------------------------


def test_removing_a_condition_from_the_file_removes_it(catalogue):
    """Otherwise the file is a lie: it would read narrower than reality."""
    apply_spec(
        Spec(permissions={
            "p": {"name": "p", "rules": [{
                "key": "widget.update", "label": "both",
                "conditions": [{"filter": "widget.warehouse"}, {"filter": "widget.assigned"}],
            }]}
        })
    )
    assert PermissionRuleCondition.objects.count() == 2

    apply_spec(
        Spec(permissions={
            "p": {"name": "p", "rules": [{
                "key": "widget.update", "label": "both",
                "conditions": [{"filter": "widget.warehouse"}],
            }]}
        })
    )

    assert [c.filter.key for c in PermissionRuleCondition.objects.all()] == [
        "widget.warehouse"
    ]


def test_a_permission_the_spec_does_not_name_is_left_alone(catalogue):
    """A spec manages its own permissions, not the whole table."""
    hand_made = Permission.objects.create(key="made-in-the-admin", name="By hand")

    apply_spec(KEEPER)

    assert Permission.objects.filter(pk=hand_made.pk).exists()


def test_a_role_binding_added_by_hand_survives_a_re_run(catalogue):
    """A deploy must not revoke what someone granted in the admin an hour ago."""
    apply_spec(KEEPER)
    extra = Permission.objects.create(key="granted-in-the-admin", name="Extra")
    RolePermission.objects.create(role=Role.objects.get(key="w_keeper"), permission=extra)

    apply_spec(KEEPER)

    assert RolePermission.objects.filter(permission=extra).exists()


# -- it refuses what the catalogue cannot honour --------------------------


def test_a_filter_the_code_does_not_declare_is_refused(catalogue):
    with pytest.raises(ConfigurationError, match="No filter"):
        apply_spec(Spec(permissions={"p": {"name": "p", "rules": [
            {"key": "widget.view", "conditions": [{"filter": "widget.invented"}]}
        ]}}))


def test_a_retired_filter_is_refused(catalogue):
    """It still resolves for rules that have it; it cannot be newly granted."""
    RegisteredFilter.objects.filter(key="widget.warehouse").update(is_live=False)

    with pytest.raises(ConfigurationError, match="retired"):
        apply_spec(KEEPER)


def test_a_filter_from_another_object_is_refused(catalogue):
    with pytest.raises(ConfigurationError, match="wrong column"):
        apply_spec(Spec(permissions={"p": {"name": "p", "rules": [
            {"key": "widget.view", "conditions": [{"filter": "crate.named",
                                                   "params": {"names": ["c"]}}]}
        ]}}))


def test_params_are_validated_against_the_declaration(catalogue):
    with pytest.raises(InvalidParams):
        apply_spec(Spec(permissions={"p": {"name": "p", "rules": [
            {"key": "widget.view", "conditions": [
                {"filter": "widget.status_in", "params": {"wrong": ["DRAFT"]}}
            ]}
        ]}}))


def test_nothing_is_written_when_the_spec_is_bad(catalogue):
    """Atomic, so a bad reference leaves no half-built role behind."""
    with pytest.raises(ConfigurationError):
        apply_spec(Spec(permissions={
            "good": {"name": "Fine", "endpoints": ["widget.view"]},
            "bad": {"name": "Bad", "rules": [
                {"key": "widget.view", "conditions": [{"filter": "widget.invented"}]}
            ]},
        }))

    assert not Permission.objects.exists()


def test_an_empty_catalogue_says_to_sync_first(db):
    with pytest.raises(ConfigurationError, match="permkit_sync"):
        apply_spec(KEEPER)


def test_two_rules_for_one_key_need_distinct_labels(catalogue):
    """The label is what tells one rule from another when re-applying."""
    with pytest.raises(ConfigurationError, match="different labels"):
        apply_spec(Spec(permissions={"p": {"name": "p", "rules": [
            {"key": "widget.view", "conditions": [{"filter": "widget.own"}]},
            {"key": "widget.view", "conditions": [{"filter": "widget.assigned"}]},
        ]}}))


def test_two_labelled_rules_union(catalogue, make_user, widgets):
    """Which is the point of allowing two: more rules widen access."""
    apply_spec(Spec(
        permissions={"p": {
            "name": "p",
            "endpoints": ["widget.view"],
            "rules": [
                {"key": "widget.view", "label": "mine",
                 "conditions": [{"filter": "widget.assigned"}]},
                {"key": "widget.view", "label": "my warehouse",
                 "conditions": [{"filter": "widget.warehouse"}]},
            ],
        }},
        roles={"w_two": {"permissions": ["p"]}},
    ))

    user = make_user(role="w_two", warehouse="KHO_2")
    assert PermissionRule.objects.count() == 2
    assert set(widget_list(fetched_by=user).values_list("name", flat=True)) == {"kho2"}


# -- field grants ---------------------------------------------------------


def test_a_field_grant_needs_an_endpoint(catalogue):
    """Reading and writing a field are separate grants, always."""
    with pytest.raises(ConfigurationError, match="needs an 'endpoint'"):
        apply_spec(Spec(permissions={"p": {"name": "p", "fields": [
            {"group": "widget.money"}
        ]}}))


def test_a_field_grant_is_applied(catalogue):
    apply_spec(Spec(permissions={"p": {"name": "p", "fields": [
        {"group": "widget.money", "endpoint": "view"}
    ]}}))

    grant = PermissionFieldGrant.objects.get()
    assert grant.key == "widget.view"
    assert grant.field_group.fields == ["secret_price"]


# -- the command ----------------------------------------------------------


SPEC_FILE = '''
PERMISSIONS = {
    "from-a-file": {
        "name": "Loaded from a path",
        "endpoints": ["widget.view"],
    }
}
ROLES = {"w_file": {"label": "File role", "permissions": ["from-a-file"]}}
'''


def test_the_command_applies_a_spec_from_a_file(catalogue, tmp_path):
    path = tmp_path / "baseline.py"
    path.write_text(SPEC_FILE)

    call_command("permkit_apply", str(path), verbosity=0)

    assert Permission.objects.filter(key="from-a-file").exists()
    assert Role.objects.filter(key="w_file").exists()


def test_check_writes_nothing_and_fails_when_out_of_step(catalogue, tmp_path):
    path = tmp_path / "baseline.py"
    path.write_text(SPEC_FILE)

    with pytest.raises(CommandError, match="out of step"):
        call_command("permkit_apply", str(path), "--check", verbosity=0)

    assert not Permission.objects.filter(key="from-a-file").exists()


def test_check_passes_once_applied(catalogue, tmp_path):
    path = tmp_path / "baseline.py"
    path.write_text(SPEC_FILE)
    call_command("permkit_apply", str(path), verbosity=0)

    call_command("permkit_apply", str(path), "--check", verbosity=0)


def test_a_missing_file_is_a_clean_error(catalogue):
    with pytest.raises(CommandError, match="No such spec file"):
        call_command("permkit_apply", "nope.py", verbosity=0)


def test_a_bad_reference_is_a_sentence_not_a_traceback(catalogue, tmp_path):
    path = tmp_path / "bad.py"
    path.write_text('PERMISSIONS = {"p": {"name": "p", "endpoints": ["widget.invented"]}}')

    with pytest.raises(CommandError, match="No endpoint"):
        call_command("permkit_apply", str(path), verbosity=0)
