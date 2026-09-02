"""Grant storage — the configurable half of the system.

Three tables, deliberately not joined to one another.  An object grant and a
field grant never touch, so there is no "do this grant's fields apply to that
grant's rows?" ambiguity: field visibility resolves once per request from the
role, independent of which rows matched.

Read and write are separated by *key*, not by a column here — ``order.view``
and ``order.update`` are different keys carrying different grants.
"""

from __future__ import annotations

from django.db import models


def normalize_role(role: str) -> str:
    """Canonical form for a role id.

    Role strings arrive from wildly inconsistent sources (``"MANAGER"``,
    ``"sale_staff"``), and comparing them raw is how permission checks
    silently fail.  Normalisation happens here, once.
    """
    return role.strip().lower()


class ObjectGrant(models.Model):
    """A named row-level grant: ``key`` + conditions that are AND-ed together."""

    name = models.SlugField(max_length=200, unique=True)
    key = models.CharField(max_length=200, db_index=True)
    #: ``[{"condition": "object.owned_by", "params": {"field": "owner"}}, ...]``
    #: Conditions within one grant intersect; grants themselves union.
    conditions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.key})"


class FieldGrant(models.Model):
    """A named field grant: an ALLOW-list of fields for one key.

    Allow-lists rather than hide-lists so the system stays monotonic — holding
    an extra grant can only ever reveal more.  With hide-lists, unioning two
    grants would *shrink* what you see, and nobody can reason about that.
    """

    name = models.SlugField(max_length=200, unique=True)
    key = models.CharField(max_length=200, db_index=True)
    allowed_fields = models.JSONField(default=list, blank=True)
    #: ``{"type": ["MATERIAL", "OUTSOURCE"]}`` — permitted values for a field.
    #: A grant that lists a field in ``allowed_fields`` without constraining it
    #: here does NOT lift another grant's constraint on that field.
    allowed_values = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.key})"


class RoleEndpointGrant(models.Model):
    """Role may attempt this key at all. The endpoint tier needs no payload."""

    role = models.CharField(max_length=100, db_index=True)
    key = models.CharField(max_length=200, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("role", "key"), name="permkit_unique_role_endpoint"
            )
        ]

    def __str__(self) -> str:
        return f"{self.role} → {self.key}"


class RoleObjectGrant(models.Model):
    role = models.CharField(max_length=100, db_index=True)
    grant = models.ForeignKey(
        ObjectGrant, on_delete=models.CASCADE, related_name="role_bindings"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("role", "grant"), name="permkit_unique_role_object"
            )
        ]

    def __str__(self) -> str:
        return f"{self.role} → {self.grant.name}"


class RoleFieldGrant(models.Model):
    role = models.CharField(max_length=100, db_index=True)
    grant = models.ForeignKey(
        FieldGrant, on_delete=models.CASCADE, related_name="role_bindings"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("role", "grant"), name="permkit_unique_role_field"
            )
        ]

    def __str__(self) -> str:
        return f"{self.role} → {self.grant.name}"
