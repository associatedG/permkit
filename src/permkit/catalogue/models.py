"""The catalogue tables — one row per thing the code declares.

Written only by ``permkit_sync``.  Read by the admin (tier 4) and, from tier 3
onward, pointed at by foreign keys from the composition tables: that is the
whole reason these exist.  A grant cannot hold a foreign key into a Python
dict, so the registry has to become data before anything can be composed
from it.

Two columns are on every row:

``last_seen_at``
    When the declaration was last found in code.

``is_live``
    False once a sync run no longer finds it.  Rows are **never deleted**: a
    composition that references a filter somebody removed keeps resolving
    exactly as it did yesterday, while showing up as broken in the admin and
    failing the next sync's validation.  Deleting would either cascade the
    composition away silently or block the sync outright, and neither is a
    thing to discover during a deploy.
"""

from __future__ import annotations

from django.db import models


class CatalogueEntry(models.Model):
    """Common shape: a human label, plus the liveness pair."""

    label = models.CharField(max_length=200, blank=True)
    last_seen_at = models.DateTimeField()
    is_live = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True


class RegisteredObject(CatalogueEntry):
    """A permissioned object — what filters and field groups are *about*.

    Declared by ``permission_object("widget", model=Widget)``.
    """

    key = models.SlugField(max_length=200, unique=True)
    #: ``"dummy.Widget"``.  Blank when the object key was mentioned by a
    #: selector or serializer but never bound to a model.
    model_label = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("key",)
        verbose_name = "declared object"
        verbose_name_plural = "declared objects"

    def __str__(self) -> str:
        return self.key


class RegisteredFilter(CatalogueEntry):
    """A row rule an admin may compose into a grant.

    Declared by ``@object_condition`` or by a selector's ``filters=`` mapping.
    """

    object = models.ForeignKey(
        RegisteredObject, on_delete=models.CASCADE, related_name="filters"
    )
    #: The full condition id — ``"widget.warehouse"``.  Compositions reference
    #: filters by this string, so it is what the stale-reference check joins on.
    key = models.CharField(max_length=200, unique=True)
    #: ``{"values": {"type": "list", "required": true}}`` — the params an admin
    #: fills in when composing, rendered as the form for this filter.
    open_params = models.JSONField(default=dict, blank=True)
    #: True when the rule traverses a to-many relation, so the resolver adds
    #: ``.distinct()``.  Surfaced here because it changes what a composed rule
    #: costs to run.
    multi_valued = models.BooleanField(default=False)

    class Meta:
        ordering = ("object__key", "key")
        verbose_name = "declared row filter"
        verbose_name_plural = "declared row filters"

    def __str__(self) -> str:
        return self.key


class RegisteredEndpoint(CatalogueEntry):
    """An endpoint — declared by ``@api_permission``.

    ``targets`` lists every component that enforces it.  An endpoint with no
    target cannot be registered at all, which is what makes "a catalogue entry
    nobody honours" unrepresentable rather than merely detectable.
    """

    key = models.CharField(max_length=200, unique=True)
    targets = models.JSONField(default=list, blank=True)

    class Meta:
        # The model was called RegisteredAction until the vocabulary was
        # unified; "endpoint" is what the tier is called everywhere else.
        ordering = ("key",)
        verbose_name = "declared endpoint"
        verbose_name_plural = "declared endpoints"

    def __str__(self) -> str:
        return self.key


class RegisteredFieldGroup(CatalogueEntry):
    """A named bundle of fields — declared as ``permission_fields``.

    Groups rather than columns so an admin grants *"Money"*, and so adding a
    column to the group updates every role that already granted it.
    """

    object = models.ForeignKey(
        RegisteredObject, on_delete=models.CASCADE, related_name="field_groups"
    )
    key = models.CharField(max_length=100)
    fields = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("object__key", "key")
        verbose_name = "declared field group"
        verbose_name_plural = "declared field groups"
        constraints = [
            models.UniqueConstraint(
                fields=("object", "key"), name="permkit_unique_field_group"
            )
        ]

    def __str__(self) -> str:
        return f"{self.object.key}.{self.key}"


class RegisteredScopePoint(CatalogueEntry):
    """A place where an object's filters are actually applied.

    Recorded so the catalogue can answer the one question the filter rows
    cannot: does this object's filtering happen *anywhere*?  Filters on an
    object with no scope point can be composed by an admin and will silently
    do nothing, which is the failure this whole tier exists to make loud.

    Several sites may scope the same pair — a list view and an export — so the
    identity includes the target.
    """

    object = models.ForeignKey(
        RegisteredObject, on_delete=models.CASCADE, related_name="scope_points"
    )
    endpoint_key = models.CharField(max_length=100)
    #: ``"tests.dummy.selectors.widget_list"`` — the selector that applies it.
    target = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("object__key", "endpoint_key", "target")
        verbose_name = "declared enforcement point"
        verbose_name_plural = "declared enforcement points"
        constraints = [
            models.UniqueConstraint(
                fields=("object", "endpoint_key", "target"),
                name="permkit_unique_scope_point",
            )
        ]

    @property
    def key(self) -> str:
        return f"{self.object.key}.{self.endpoint_key}"

    def __str__(self) -> str:
        return f"{self.key} @ {self.target}"
