"""Tier 4 — the screens.

Two things are worth testing about an admin, and neither is "does Django
render a form". The first is that the catalogue cannot be edited, because an
edit there is silently reverted by the next sync. The second is that the
dropdowns are narrowed: the whole reason composition moved off free text is
that a filter belonging to another object, or one that no longer exists, must
be *unreachable* rather than merely detected afterwards.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from permkit.admin import (
    PermissionRuleAdmin,
    PermissionRuleConditionInline,
    RegisteredFilterAdmin,
)
from permkit.catalogue.models import RegisteredFilter, RegisteredObject
from permkit.catalogue.sync import sync_catalogue
from permkit.models import Permission, PermissionRule

from .dummy.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db) -> User:
    return User.objects.create_superuser(
        username="staff", password="x", role="w_admin"
    )


@pytest.fixture
def catalogue(db):
    return sync_catalogue()


@pytest.fixture
def rule(catalogue) -> PermissionRule:
    return PermissionRule.objects.create(
        permission=Permission.objects.create(key="p", name="Composed"),
        object=RegisteredObject.objects.get(key="widget"),
        endpoint_key="view",
    )


# -- the catalogue is not editable ----------------------------------------


def test_catalogue_pages_are_read_only(staff, catalogue, client):
    """An edit here would be reverted by the next sync without a word."""
    client.force_login(staff)
    request = RequestFactory().get("/")
    request.user = staff
    catalogue_admin = RegisteredFilterAdmin(RegisteredFilter, django_admin.site)

    assert catalogue_admin.has_add_permission(request) is False
    assert catalogue_admin.has_change_permission(request) is False
    assert catalogue_admin.has_delete_permission(request) is False

    assert client.get(reverse("admin:permkit_registeredfilter_add")).status_code == 403


@pytest.mark.parametrize(
    "route",
    [
        "admin:permkit_registeredobject_changelist",
        "admin:permkit_registeredfilter_changelist",
        "admin:permkit_registeredendpoint_changelist",
        "admin:permkit_registeredfieldgroup_changelist",
        "admin:permkit_registeredscopepoint_changelist",
        "admin:permkit_permission_changelist",
        "admin:permkit_permissionrule_changelist",
        "admin:permkit_role_changelist",
    ],
)
def test_every_page_renders(staff, catalogue, client, route):
    client.force_login(staff)

    assert client.get(reverse(route)).status_code == 200


def test_a_permission_page_renders_its_rules_and_the_union_rule(staff, rule, client):
    client.force_login(staff)

    response = client.get(
        reverse("admin:permkit_permission_change", args=[rule.permission_id])
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "widget.view" in body
    # The one thing an administrator has to hold in their head.
    assert "any" in body and "all" in body


# -- the dropdowns are narrowed -------------------------------------------


def test_the_filter_dropdown_offers_only_this_object_s_filters(staff, rule):
    """A crate filter on a widget rule filters on the wrong column, silently.

    Validation catches it after the fact; this is what puts it out of reach.
    """
    request = RequestFactory().get("/")
    request.user = staff
    inline = PermissionRuleConditionInline(PermissionRule, django_admin.site)
    inline.get_formset(request, rule)

    field = inline.formfield_for_foreignkey(
        PermissionRuleConditionInline.model._meta.get_field("filter"), request
    )
    offered = set(field.queryset.values_list("key", flat=True))

    assert offered == set(
        RegisteredFilter.objects.filter(object__key="widget").values_list(
            "key", flat=True
        )
    )
    assert not any(k.startswith("crate.") for k in offered)


def test_a_retired_filter_is_not_offered(staff, rule):
    """It still resolves for rules that already use it; it is not composable."""
    RegisteredFilter.objects.filter(key="widget.own").update(is_live=False)
    request = RequestFactory().get("/")
    request.user = staff
    inline = PermissionRuleConditionInline(PermissionRule, django_admin.site)
    inline.get_formset(request, rule)

    field = inline.formfield_for_foreignkey(
        PermissionRuleConditionInline.model._meta.get_field("filter"), request
    )

    assert "widget.own" not in set(field.queryset.values_list("key", flat=True))


def test_the_rule_page_lists_the_params_each_filter_expects(staff, rule):
    """``params`` is free-form JSON; the declared schema is what makes it typeable."""
    rendered = PermissionRuleAdmin(
        PermissionRule, django_admin.site
    ).filter_reference(rule)

    assert "widget.status_in" in rendered
    # Escaped, because it is data: it renders as `"values": <list>`.
    assert "&quot;values&quot;: &lt;list&gt;" in rendered
    assert "crate.named" not in rendered


# -- the preview ----------------------------------------------------------


def test_the_preview_explains_a_denial(staff, catalogue, client, make_user, widgets):
    """The screen that makes composition trustworthy: ask, and read the reason."""
    from django.core.management import call_command

    call_command("seed_dummy_roles", verbosity=0)
    keeper = make_user(role="w_keeper", warehouse="KHO_1")
    client.force_login(staff)

    response = client.get(
        reverse("admin:permkit_preview"),
        {
            "user": keeper.pk,
            "key": "widget.update",
            "object_pk": widgets["kho1_unassigned"].pk,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "OUT of scope" in body
    assert "Denied" in body


def test_the_preview_renders_empty_before_anything_is_chosen(staff, catalogue, client):
    client.force_login(staff)

    response = client.get(reverse("admin:permkit_preview"))

    assert response.status_code == 200


def test_the_preview_is_staff_only(catalogue, client):
    """It reveals who can do what, which is not public information."""
    response = client.get(reverse("admin:permkit_preview"))

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]
