"""Tier 2 and 3 — composition and assignment: the configurable half.

Everything here is edited by people, and nothing regenerates it. That is the
line between this module and :mod:`permkit.catalogue.models`, whose rows are a
projection of code and are rewritten by ``permkit_sync`` on every deploy.

The shape is one grouping entity and its parts:

    Permission                what a job needs, as one grantable thing
      PermissionEndpoint        endpoints it may reach
      PermissionRule          rows it may act on — several, OR-ed
        PermissionRuleCondition   narrowing within one rule — AND-ed
      PermissionFieldGrant    fields it may see or write
    RolePermission            which roles hold it

Semantics are unchanged from the free-text tables these replace, because the
resolver is unchanged: **rules union, conditions within a rule intersect,
field groups union.** More rules can only ever add access; more conditions on
a rule can only ever remove it. That is what makes "why can this user do X?"
an answerable question.

Every reference into the catalogue is a foreign key, which is the whole point
of tier 1. A condition cannot name a filter that does not exist, and the admin
dropdown can be narrowed to the filters that are live *and* belong to the
object the rule is about — so the two ways a hand-typed rule used to fail
silently are now unrepresentable.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

# Foreign-key targets for everything below. Importing them here is also what
# registers the catalogue models with Django, since they live one module down;
# ``RegisteredScopePoint`` has no foreign key pointing at it and is imported
# for that reason alone.
from .catalogue.models import (  # noqa: F401
    RegisteredEndpoint,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
    RegisteredScopePoint,
)


def normalize_role(role: str) -> str:
    """Canonical form for a role id.

    Role strings arrive from wildly inconsistent sources (``"MANAGER"``,
    ``"sale_staff"``), and comparing them raw is how permission checks
    silently fail. Normalisation happens here, once.
    """
    return role.strip().lower()


# -- tier 3: who ----------------------------------------------------------


class Role(models.Model):
    """A role id, enumerated so it can be chosen rather than typed.

    permkit does not own how a user *gets* a role — ``PrincipalResolver`` is a
    seam precisely because one project stores a string on the user, another a
    many-to-many, a third a JWT claim. This table does not change that: it is
    the list of role ids the system knows about, so the admin can offer a
    dropdown instead of a text box.

    Without it, assigning a permission means typing ``w_keeper`` by hand, and
    a typo grants nothing to nobody with no error anywhere — the same silent
    failure the catalogue exists to remove from the other three tiers.
    """

    key = models.SlugField(max_length=100, unique=True)
    label = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("key",)

    def save(self, *args, **kwargs):
        # Normalised on the way in, so what the resolver compares against and
        # what an admin sees are the same string.
        self.key = normalize_role(self.key)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.label or self.key


# -- tier 2: what ---------------------------------------------------------


class Permission(models.Model):
    """A grantable bundle — the abstract role.

    The entity the free-text tables were missing. Without it, giving someone
    "warehouse keeper" means ticking a dozen unrelated grants and hoping the
    set is complete; with it, the bundle is composed once, reviewed once, and
    assigned as one thing.

    Deliberately *not* the same as a role: several roles may hold the same
    permission, and a role may hold several. That is also how the library
    avoids role inheritance — a manager holds the keeper's permission plus an
    unscoped one, and grants union.
    """

    key = models.SlugField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(
        blank=True,
        help_text="What this permission is for, in the words of whoever grants it.",
    )

    class Meta:
        ordering = ("key",)

    def __str__(self) -> str:
        return self.name or self.key


class PermissionEndpoint(models.Model):
    """An endpoint this permission may reach. The endpoint tier needs no payload."""

    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="endpoints"
    )
    endpoint = models.ForeignKey(
        RegisteredEndpoint, on_delete=models.PROTECT, related_name="granted_by"
    )

    class Meta:
        ordering = ("endpoint__key",)
        constraints = [
            models.UniqueConstraint(
                fields=("permission", "endpoint"), name="permkit_unique_permission_endpoint"
            )
        ]

    def __str__(self) -> str:
        return f"{self.permission.key} → {self.endpoint.key}"


class PermissionRule(models.Model):
    """One object grant: which rows, for one key.

    A permission may hold several rules for the same key. They **union** — so
    "widgets in my warehouse" plus "widgets assigned to me" grants both sets,
    not their overlap. A rule with no conditions means *every* row, and the
    resolver short-circuits on it rather than OR-ing an empty ``Q``, which
    Django would collapse into a narrowing.
    """

    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="rules"
    )
    object = models.ForeignKey(
        RegisteredObject, on_delete=models.PROTECT, related_name="rules"
    )
    #: The verb half of the key — ``"view"``, ``"update"``. Read and write
    #: are different keys carrying different rules, so the rows someone may
    #: see and the rows they may edit are configured independently.
    endpoint_key = models.CharField(max_length=100)
    label = models.CharField(
        max_length=200,
        blank=True,
        help_text="How this rule reads in a list, e.g. 'own warehouse, assigned to me'.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("permission__key", "order", "pk")

    @property
    def key(self) -> str:
        return f"{self.object.key}.{self.endpoint_key}"

    @property
    def name(self) -> str:
        """Stable id for the explain trace, so a denial names a specific rule."""
        return f"{self.permission.key}#{self.order}"

    def __str__(self) -> str:
        return f"{self.key}: {self.label or 'every row'}"


class PermissionRuleCondition(models.Model):
    """One filter inside a rule. Conditions on a rule are AND-ed.

    ``params`` are config-time values for the filter's declared parameters —
    never request-time data. The acting user and their warehouse arrive
    through ``Context`` instead, which is what keeps user data out of grant
    configuration.
    """

    rule = models.ForeignKey(
        PermissionRule, on_delete=models.CASCADE, related_name="conditions"
    )
    filter = models.ForeignKey(
        RegisteredFilter, on_delete=models.PROTECT, related_name="used_by"
    )
    params = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("rule", "filter"), name="permkit_unique_rule_filter"
            )
        ]

    def clean(self) -> None:
        """Reject at edit time what would otherwise fail at request time.

        Two things. A filter declared for one object applied to a rule about
        another compiles happily and filters on the wrong column — plausible
        rows, no error. And params that do not match the filter's declared
        schema raise only when someone finally exercises the rule, which may
        be months later and will not look like a configuration problem.
        """
        from .registry import registry

        if self.filter_id and self.rule_id and self.filter.object_id != self.rule.object_id:
            raise ValidationError(
                {
                    "filter": (
                        f"{self.filter.key} filters {self.filter.object.key}, but "
                        f"this rule is about {self.rule.object.key}."
                    )
                }
            )
        if self.filter_id and registry.has_condition(self.filter.key):
            try:
                registry.condition(self.filter.key).bind(self.params or {})
            except Exception as exc:
                raise ValidationError({"params": str(exc)}) from exc

    def __str__(self) -> str:
        return self.filter.key


class PermissionFieldGrant(models.Model):
    """A field group this permission may see or write, for one key.

    Carries an ``endpoint_key`` rather than a READ/WRITE mode, because the keys
    are finer than that pair: ``widget.update`` and ``widget.create`` are both
    writes and are deliberately separate grants — being allowed to edit a
    price on an existing row does not mean being allowed to set one on a new
    row. A mode column would silently merge them.
    """

    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="field_grants"
    )
    field_group = models.ForeignKey(
        RegisteredFieldGroup, on_delete=models.PROTECT, related_name="granted_by"
    )
    endpoint_key = models.CharField(max_length=100)
    #: ``{"status": ["DRAFT", "ACTIVE"]}`` — permitted values for a field.
    #: A grant that allows the field without constraining it here does NOT
    #: lift another grant's constraint: silence is not "any value".
    allowed_values = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("field_group__object__key", "endpoint_key", "field_group__key")
        constraints = [
            models.UniqueConstraint(
                fields=("permission", "field_group", "endpoint_key"),
                name="permkit_unique_permission_field_grant",
            )
        ]

    @property
    def key(self) -> str:
        return f"{self.field_group.object.key}.{self.endpoint_key}"

    def __str__(self) -> str:
        return f"{self.key}: {self.field_group.key}"


class RolePermission(models.Model):
    """Tier 3 — which roles hold which permissions. Deliberately dull."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_bindings"
    )

    class Meta:
        ordering = ("role__key", "permission__key")
        constraints = [
            models.UniqueConstraint(
                fields=("role", "permission"), name="permkit_unique_role_permission"
            )
        ]

    def __str__(self) -> str:
        return f"{self.role.key} → {self.permission.key}"
