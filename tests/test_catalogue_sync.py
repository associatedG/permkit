"""Tier 1 — the registry, published as rows.

The catalogue is a projection of code, so the properties worth testing are the
ones that decide whether an admin can trust what they are looking at:

* everything the registry holds reaches the tables
* re-running changes nothing, so a deploy that syncs twice is not a diff
* a declaration that leaves the code is retired, never deleted, so the
  composition pointing at it stays findable
* the ways code and configuration can drift apart fail the run
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Q

from permkit.base import ObjectCondition
from permkit.catalogue.loading import load_declarations
from permkit.catalogue.models import (
    RegisteredAction,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
    RegisteredScopePoint,
)
from permkit.catalogue.sync import sync_catalogue
from permkit.models import ObjectGrant, RoleEndpointGrant
from permkit.registry import Registry, registry as live_registry

from .dummy.models import Widget

pytestmark = pytest.mark.django_db


def codes(report) -> list[str]:
    return [p.code for p in report.problems]


# -- an isolated registry, so a test cannot leak into the live one ---------


class _Anything(ObjectCondition):
    """Everything."""

    def as_q(self, ctx, **params) -> Q:
        return Q()


def make_registry(*, scope_point: bool = True, fields=("secret_price",)) -> Registry:
    reg = Registry()
    reg.register_object("gadget", model=Widget, label="Gadgets")
    reg.register_condition("gadget.anything", object_key="gadget")(_Anything)
    reg.register_field_group("gadget", "money", fields=fields)
    reg.register_action("gadget.view", label="List gadgets", target="somewhere.View")
    if scope_point:
        reg.register_scope_point("gadget", "view", target="somewhere.gadget_list")
    return reg


# -- publishing -----------------------------------------------------------


def test_sync_publishes_every_declaration_the_registry_holds():
    """The catalogue is complete, or composition is built on a partial view."""
    report = sync_catalogue()

    assert set(RegisteredObject.objects.values_list("key", flat=True)) == set(
        live_registry.objects
    )
    assert set(RegisteredAction.objects.values_list("key", flat=True)) == set(
        live_registry.actions
    )
    assert set(RegisteredFilter.objects.values_list("key", flat=True)) == set(
        live_registry.conditions
    )
    assert RegisteredFieldGroup.objects.count() == len(live_registry.field_groups)
    assert RegisteredScopePoint.objects.count() == sum(
        len(v) for v in live_registry.scope_points.values()
    )
    assert not report.errors


def test_a_filter_carries_what_an_admin_needs_to_compose_it():
    """The label and the param schema are the admin form for this filter."""
    sync_catalogue()

    row = RegisteredFilter.objects.get(key="widget.status_in")
    assert row.object.key == "widget"
    assert row.label == "Widgets in one of the chosen statuses."
    assert row.open_params == {"values": {"type": "list", "required": True}}
    assert row.multi_valued is False

    # ``.distinct()`` is not a detail of the query here: it is a property of
    # the rule, and the admin composing it should see it.
    assert RegisteredFilter.objects.get(
        key="widget.watched_by_my_warehouse"
    ).multi_valued is True


def test_an_action_records_every_component_that_enforces_it():
    sync_catalogue()

    row = RegisteredAction.objects.get(key="widget.view")
    assert sorted(row.targets) == [
        "tests.dummy.views.WidgetDetailApi",
        "tests.dummy.views.WidgetListApi",
    ]


def test_an_object_is_published_with_the_model_it_stands_for():
    sync_catalogue()

    assert RegisteredObject.objects.get(key="widget").model_label == "dummy.Widget"


# -- idempotence ----------------------------------------------------------


def test_re_running_sync_changes_nothing():
    """A deploy that syncs twice must not look like a deploy that changed something."""
    sync_catalogue()
    ids = sorted(RegisteredFilter.objects.values_list("pk", flat=True))

    second = sync_catalogue()

    assert not second.changed
    for table in second.tables:
        assert (table.created, table.updated, table.revived, table.retired) == (
            0,
            0,
            0,
            0,
        ), table.name
    assert sorted(RegisteredFilter.objects.values_list("pk", flat=True)) == ids


def test_a_changed_declaration_is_an_update_not_a_new_row():
    sync_catalogue(registry=make_registry(), load=False)
    pk = RegisteredFieldGroup.objects.get(key="money").pk

    report = sync_catalogue(
        registry=make_registry(fields=("secret_price", "notes")), load=False
    )

    groups = next(t for t in report.tables if t.name == "field groups")
    assert (groups.created, groups.updated) == (0, 1)
    row = RegisteredFieldGroup.objects.get(pk=pk)
    assert row.fields == ["secret_price", "notes"]


# -- retirement -----------------------------------------------------------


def test_a_declaration_removed_from_code_is_retired_not_deleted():
    """A composition pointing at it must keep working while surfacing as broken.

    Deleting would either cascade the grant away without a word or block the
    sync outright — both worse things to meet during a deploy than a row
    flagged dead.
    """
    sync_catalogue(registry=make_registry(), load=False)

    report = sync_catalogue(registry=Registry(), load=False)

    row = RegisteredFilter.objects.get(key="gadget.anything")
    assert row.is_live is False
    assert next(t for t in report.tables if t.name == "filters").retired == 1


def test_a_declaration_that_comes_back_is_revived():
    sync_catalogue(registry=make_registry(), load=False)
    sync_catalogue(registry=Registry(), load=False)

    report = sync_catalogue(registry=make_registry(), load=False)

    assert RegisteredFilter.objects.get(key="gadget.anything").is_live is True
    filters = next(t for t in report.tables if t.name == "filters")
    assert (filters.revived, filters.created) == (1, 0)


# -- validation -----------------------------------------------------------


def test_filters_on_an_object_nothing_scopes_fail_the_run():
    """Those filters can never fire, however carefully an admin composes them."""
    report = sync_catalogue(registry=make_registry(scope_point=False), load=False)

    assert "filters-never-fire" in codes(report)
    assert "gadget.anything" in str(report.errors[0])


def test_a_field_group_naming_a_field_the_model_lacks_fails_the_run():
    report = sync_catalogue(
        registry=make_registry(fields=("secret_price", "nonexistent")), load=False
    )

    assert "unknown-field" in codes(report)


def test_a_computed_attribute_is_a_legitimate_field_to_group():
    """Field stripping works on the serialized payload, so a property counts."""
    report = sync_catalogue(
        registry=make_registry(fields=("secret_price", "pk")), load=False
    )

    assert "unknown-field" not in codes(report)


def test_a_composition_referencing_a_removed_filter_is_flagged():
    ObjectGrant.objects.create(
        name="legacy-rule",
        key="widget.view",
        conditions=[{"condition": "widget.deleted_last_year", "params": {}}],
    )

    report = sync_catalogue()

    assert "stale-filter" in codes(report)


def test_a_composition_referencing_a_key_no_declaration_mentions_is_flagged():
    RoleEndpointGrant.objects.create(role="w_admin", key="widget.approve")

    report = sync_catalogue()

    assert "stale-key" in codes(report)


def test_a_filter_composed_onto_the_wrong_object_is_flagged_before_it_runs():
    """The resolver already refuses this at request time; sync moves it earlier.

    A crate filter on a widget key compiles happily and filters widgets on the
    wrong column, so the difference between catching it here and catching it
    in production is the difference between a build failure and plausible
    wrong rows.
    """
    ObjectGrant.objects.create(
        name="crossed-rule",
        key="widget.view",
        conditions=[{"condition": "crate.named", "params": {"names": ["c"]}}],
    )

    report = sync_catalogue()

    assert "misfiled-filter" in codes(report)


def test_an_object_nobody_bound_to_a_model_is_a_warning_not_a_failure():
    reg = Registry()
    reg.register_condition("orphan.anything", object_key="orphan")(_Anything)
    reg.register_scope_point("orphan", "view", target="somewhere.orphan_list")

    report = sync_catalogue(registry=reg, load=False)

    assert "unbound-object" in [p.code for p in report.warnings]
    assert not report.errors
    assert RegisteredObject.objects.get(key="orphan").model_label == ""


# -- loading --------------------------------------------------------------


def test_the_loader_reaches_declarations_no_url_resolves():
    """Sync never serves a request, so it cannot rely on the URLconf alone.

    ``services.py`` is the case that matters: reachable from a view today,
    but a service called only from a Celery task would be invisible to a
    URLconf walk, and its actions would be published dead.
    """
    report = load_declarations()

    assert "tests.dummy.services" in report.modules
    assert "tests.dummy.selectors" in report.modules
    assert report.urlconf_resolved


def test_every_declared_scope_point_and_action_survives_a_forced_load():
    load_declarations()

    assert set(live_registry.actions) >= {
        "widget.view",
        "widget.update",
        "widget.create",
        "crate.view",
    }
    assert set(live_registry.scope_points) >= {
        ("widget", "view"),
        ("widget", "update"),
        ("crate", "view"),
    }


# -- the command ----------------------------------------------------------


def test_the_command_runs_clean_against_the_dummy_domain():
    call_command("permkit_sync", verbosity=0)

    assert RegisteredFilter.objects.filter(is_live=True).count() == len(
        live_registry.conditions
    )


def test_check_writes_nothing():
    call_command("permkit_sync", verbosity=0)
    RegisteredFilter.objects.filter(key="widget.own").delete()

    with pytest.raises(CommandError, match="out of date"):
        call_command("permkit_sync", "--check", verbosity=0)

    assert not RegisteredFilter.objects.filter(key="widget.own").exists()


def test_check_passes_once_the_catalogue_is_current():
    call_command("permkit_sync", verbosity=0)

    call_command("permkit_sync", "--check", verbosity=0)


def test_the_command_fails_the_build_on_a_stale_composition():
    ObjectGrant.objects.create(
        name="legacy-rule",
        key="widget.view",
        conditions=[{"condition": "widget.deleted_last_year"}],
    )

    with pytest.raises(CommandError, match="catalogue problem"):
        call_command("permkit_sync", verbosity=0)
