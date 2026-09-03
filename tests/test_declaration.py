"""Tier 0 — what the component-level declarations guarantee.

Two properties matter here, and both exist to stop an administrator composing
a rule that silently does nothing:

* a site that declares object permissions cannot return without applying them
* a filter declared for one object cannot be composed onto another
"""

from __future__ import annotations

import pytest
from django.db.models import Q

from permkit import (
    apply_permissions,
    object_permissions,
    registry,
)
from permkit.exceptions import ConfigurationError

from .dummy.models import Widget
from .dummy.selectors import widget_list, widget_writable

pytestmark = pytest.mark.django_db


# -- declaring without enforcing ----------------------------------------


def test_a_site_that_never_applies_its_filters_raises(policy, grants, widgets, viewer):
    """Registering and enforcing cannot drift apart.

    A selector that declares object permissions and then forgets to apply them
    would hand back every row while the catalogue advertises it as guarded —
    the exact failure where an admin configures a filter that never fires.
    """

    @object_permissions("widget", "view")
    def forgetful(*, fetched_by):
        return Widget.objects.all()  # never calls apply_permissions

    with pytest.raises(ConfigurationError, match="never called apply_permissions"):
        forgetful(fetched_by=viewer)


def test_apply_permissions_outside_a_site_raises(policy, grants, viewer):
    """The filters to apply come from the enclosing declaration."""
    with pytest.raises(ConfigurationError, match="outside a function decorated"):
        apply_permissions(Widget.objects.all(), actor=viewer)


def test_a_site_requires_its_actor(policy, grants, widgets):
    with pytest.raises(TypeError, match="fetched_by"):
        widget_list()


def test_the_site_still_narrows(policy, grants, widgets, keeper_kho1):
    """The decorator adds a guarantee; it does not change the outcome."""
    visible = set(widget_list(fetched_by=keeper_kho1).values_list("name", flat=True))
    assert visible == {"kho1-assigned", "kho1-unassigned", "kho1-locked"}


def test_read_and_write_sites_are_separate(policy, grants, widgets, keeper_kho1):
    """Two sites on one object, two endpoints, two different row sets."""
    readable = set(widget_list(fetched_by=keeper_kho1).values_list("name", flat=True))
    writable = set(widget_writable(fetched_by=keeper_kho1).values_list("name", flat=True))

    assert writable < readable
    assert writable == {"kho1-assigned", "kho1-locked"}


# -- object key binding -------------------------------------------------


def test_a_filter_cannot_be_applied_to_another_object(
    policy, store, widgets, make_user
):
    """The link between an endpoint and its filters is the object key.

    Unchecked, a ``crate`` filter composed onto a ``widget`` endpoint compiles
    happily and filters widgets on ``Widget.name`` — no error, plausible rows,
    silently wrong.
    """
    store.grant_endpoint("mixed", "widget.view")
    store.grant_object(
        "mixed",
        "widget.view",
        name="wrong-object",
        conditions=[{"condition": "crate.named", "params": {"names": ["crate-1"]}}],
    )
    user = make_user(role="mixed")

    with pytest.raises(ConfigurationError, match="declared for object 'crate'"):
        widget_list(fetched_by=user)


def test_filters_carry_their_object_key(policy):
    assert registry.condition("widget.warehouse").object_key == "widget"
    assert registry.condition("crate.named").object_key == "crate"


# -- the catalogue tier 1 will publish ----------------------------------


def test_filters_are_registered_under_their_object(policy):
    widget_filters = registry.conditions_for("widget")
    assert "widget.warehouse" in widget_filters
    assert "crate.named" not in widget_filters


def test_every_object_with_filters_has_a_scope_point(policy):
    """The catalogue check for "configured but never fires".

    A filter on an object nothing scopes can be composed by an admin and will
    do nothing, anywhere.
    """
    objects_with_filters = {
        spec.object_key for spec in registry.conditions.values() if spec.object_key
    }
    uncovered = {
        obj for obj in objects_with_filters if not registry.has_scope_point(obj)
    }
    assert uncovered == set(), (
        "every object carrying filters must have somewhere those filters "
        "actually fire; crate gained one when the crate picker was routed"
    )


def test_declared_labels_reach_the_catalogue(policy):
    """Docstrings are what the admin UI shows, so they must survive registration."""
    assert registry.condition("widget.warehouse").cls.__doc__ == (
        "Widgets in the warehouse I belong to."
    )
    assert "values" in registry.condition("widget.status_in").params


def test_field_groups_and_endpoints_are_registered(policy):
    assert registry.field_groups_for("widget")["money"].fields == ("secret_price",)

    endpoints = registry.endpoints
    assert endpoints["widget.view"].label == "List widgets"
    assert endpoints["widget.update"].label == "Update a widget"
    # A create has no queryset to scope, but is still an endpoint someone
    # must be permitted to reach.
    assert "widget.create" in endpoints


# -- serializer-level declaration ---------------------------------------


def test_serializer_declares_its_own_field_groups(policy):
    """Groups live on the class that owns the fields, not in a floating call."""
    from .dummy.serializers import WidgetSerializer

    assert WidgetSerializer.permission_object == "widget"
    assert registry.field_groups_for("widget")["money"].fields == ("secret_price",)
    # The serializer also contributes which references are governed.
    assert registry.object("widget").references == {"crate": "crate.view"}


def test_serializer_derives_its_read_and_write_keys(policy):
    """Two keys, derived — the tiers stay separate without restating them."""
    from .dummy.serializers import WidgetSerializer

    assert WidgetSerializer.read_permission_key == "widget.view"
    assert WidgetSerializer.write_permission_key == "widget.update"


def test_a_subclass_does_not_re_register_inherited_groups(policy):
    """Subclassing a serializer must not collide with its parent's groups."""
    from .dummy.serializers import WidgetSerializer

    class TerseWidgetSerializer(WidgetSerializer):
        class Meta(WidgetSerializer.Meta):
            fields = ["id", "name"]

    assert TerseWidgetSerializer.read_permission_key == "widget.view"
    assert registry.field_groups_for("widget")["money"].fields == ("secret_price",)


def test_conflicting_group_definitions_are_rejected(policy):
    """Re-declaring a group with different fields is a configuration bug."""
    from permkit import field_groups
    from permkit.exceptions import DuplicateRegistration

    field_groups("widget", {"money": ["secret_price"]})  # identical: fine
    # On a throwaway object: the registry is process-wide, so registering a
    # group on "widget" here would make that field controlled for every test.
    field_groups("gadget", {"solo": "notes"})  # a bare string is a group of one

    with pytest.raises(DuplicateRegistration, match="different fields"):
        field_groups("widget", {"money": ["secret_price", "notes"]})


# -- one scoping path, not two ------------------------------------------


def test_the_api_reaches_its_rows_through_the_selector(
    policy, grants, widgets, keeper_kho1
):
    """The list endpoint and the selector must be the same code path.

    Two independent scoping paths — a selector and a view mixin — agree only
    because both happen to name the same key.  Routing the view through the
    selector means the "declared sites must apply their filters" guarantee
    covers HTTP as well, instead of stopping at the service layer.
    """
    from rest_framework.test import APIClient

    from .dummy.views import WidgetListApi

    client = APIClient()
    client.force_authenticate(user=keeper_kho1)
    via_http = {row["name"] for row in client.get("/widgets/").data}
    via_selector = set(
        widget_list(fetched_by=keeper_kho1).values_list("name", flat=True)
    )

    assert via_http == via_selector
    assert "get_queryset" in WidgetListApi.__dict__, (
        "the view must build its queryset from the selector, not from the model"
    )


def test_a_view_reading_the_model_directly_is_only_safe_via_the_mixin(policy):
    """The writable-list view is the counter-example, kept deliberately.

    It reads ``Widget.objects`` and relies on ``ScopedQuerysetMixin``.  Nothing
    in the declaration layer would notice if a future edit dropped the mixin,
    which is the gap the coverage report exists to close.

    It is a *read* route on purpose.  The mutation routes go through services
    instead, because a generic view writing via ``serializer.save()`` enforces
    the permission tiers and silently skips the domain invariant.
    """
    from permkit.drf import ScopedQuerysetMixin

    from .dummy.views import WidgetUpdateApi, WidgetWritableListApi

    assert issubclass(WidgetWritableListApi, ScopedQuerysetMixin)
    assert WidgetWritableListApi.queryset.model is Widget

    assert not issubclass(WidgetUpdateApi, ScopedQuerysetMixin), (
        "the mutation route must not write through a generic view"
    )


def test_several_components_may_enforce_one_endpoint(policy):
    """A list and its detail are one permission, declared the one way.

    Both use ``@api_permission``; the first supplies the label, the rest register
    as further places the endpoint is enforced.
    """
    from .dummy.views import WidgetDetailApi, WidgetListApi

    assert WidgetListApi.permission_key == "widget.view"
    assert WidgetDetailApi.permission_key == "widget.view"
    assert "widget.detail" not in registry.endpoints

    targets = registry.endpoints["widget.view"].targets
    assert len(targets) == 2
    assert registry.endpoints["widget.view"].label == "List widgets"


def test_relabelling_an_endpoint_is_rejected(policy):
    """Two components disagreeing about what an endpoint *is* is a config bug."""
    from permkit import api_permission
    from permkit.exceptions import DuplicateRegistration

    with pytest.raises(DuplicateRegistration, match="already labelled"):

        @api_permission("widget.view", label="Something else")
        class Rogue:
            pass


def test_no_endpoint_can_exist_without_something_enforcing_it(policy):
    """The endpoint-tier twin of the scope-point check — made structural.

    Because the only way to declare an endpoint is to decorate the component
    that enforces it, an entry an admin could grant while nothing honours it
    is unrepresentable rather than merely detectable.
    """
    orphans = {key for key, spec in registry.endpoints.items() if not spec.targets}
    assert not orphans, f"endpoints with no enforcing component: {orphans}"
