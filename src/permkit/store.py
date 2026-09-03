"""Where grants are read from.

A seam so the same resolver can run against the database (the normal case) or
an in-memory fixture (fast resolver tests, and consumers that would rather
keep rules in version control than in a table).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .models import (
    Permission,
    PermissionEndpoint,
    PermissionFieldGrant,
    PermissionRule,
    PermissionRuleCondition,
    Role,
    RolePermission,
    normalize_role,
)


@dataclass(frozen=True)
class ObjectGrantData:
    name: str
    key: str
    #: ``((condition_id, params), ...)`` — AND-ed together within this grant.
    conditions: tuple[tuple[str, Mapping], ...] = ()


@dataclass(frozen=True)
class FieldGrantData:
    name: str
    key: str
    allowed_fields: frozenset[str] = field(default_factory=frozenset)
    #: ``{field: frozenset(values)}`` — permitted values, where constrained.
    allowed_values: Mapping[str, frozenset] = field(default_factory=dict)


@runtime_checkable
class GrantStore(Protocol):
    def has_endpoint_grant(self, roles: Sequence[str], key: str) -> bool: ...

    def object_grants(
        self, roles: Sequence[str], key: str
    ) -> list[ObjectGrantData]: ...

    def field_grants(
        self, roles: Sequence[str], key: str
    ) -> list[FieldGrantData]: ...


def _parse_conditions(raw: Iterable[Mapping]) -> tuple[tuple[str, Mapping], ...]:
    return tuple((c["condition"], c.get("params", {})) for c in raw)


class DatabaseStore:
    """Resolves grants through the composition tables.

    Every query starts from the roles the principal resolver produced and
    joins out through ``RolePermission`` — so a permission nobody holds costs
    nothing, and a role with no permissions returns empty rather than open.

    The three methods return exactly what ``MemoryStore`` returns, which is
    what keeps the resolver ignorant of where rules are stored and lets the
    suite be written against the fast store.
    """

    def has_endpoint_grant(self, roles: Sequence[str], key: str) -> bool:
        if not roles:
            return False
        return PermissionEndpoint.objects.filter(
            permission__role_bindings__role__key__in=roles, endpoint__key=key
        ).exists()

    def object_grants(self, roles: Sequence[str], key: str) -> list[ObjectGrantData]:
        if not roles:
            return []
        object_key, _, endpoint_key = key.rpartition(".")
        rules = (
            PermissionRule.objects.filter(
                permission__role_bindings__role__key__in=roles,
                object__key=object_key,
                endpoint_key=endpoint_key,
            )
            .select_related("permission", "object")
            .prefetch_related("conditions__filter")
            .distinct()
        )
        return [
            ObjectGrantData(
                name=rule.name,
                key=key,
                conditions=tuple(
                    (c.filter.key, c.params or {}) for c in rule.conditions.all()
                ),
            )
            for rule in rules
        ]

    def field_grants(self, roles: Sequence[str], key: str) -> list[FieldGrantData]:
        if not roles:
            return []
        object_key, _, endpoint_key = key.rpartition(".")
        grants = (
            PermissionFieldGrant.objects.filter(
                permission__role_bindings__role__key__in=roles,
                field_group__object__key=object_key,
                endpoint_key=endpoint_key,
            )
            .select_related("permission", "field_group")
            .distinct()
        )
        return [
            FieldGrantData(
                name=f"{g.permission.key}/{g.field_group.key}",
                key=key,
                allowed_fields=frozenset(g.field_group.fields or ()),
                allowed_values={
                    f: frozenset(v) for f, v in (g.allowed_values or {}).items()
                },
            )
            for g in grants
        ]


class MemoryStore:
    """In-memory grants. Used by the library's own tests and by seeding."""

    def __init__(self) -> None:
        self._endpoint: set[tuple[str, str]] = set()
        self._object: list[tuple[str, ObjectGrantData]] = []
        self._field: list[tuple[str, FieldGrantData]] = []

    # -- building ---------------------------------------------------------

    def grant_endpoint(self, role: str, key: str) -> "MemoryStore":
        self._endpoint.add((normalize_role(role), key))
        return self

    def grant_object(
        self,
        role: str,
        key: str,
        *,
        name: str,
        conditions: Iterable[Mapping] = (),
    ) -> "MemoryStore":
        self._object.append(
            (
                normalize_role(role),
                ObjectGrantData(
                    name=name, key=key, conditions=_parse_conditions(conditions)
                ),
            )
        )
        return self

    def grant_field(
        self,
        role: str,
        key: str,
        *,
        name: str,
        allowed_fields: Iterable[str],
        allowed_values: Mapping[str, Iterable] | None = None,
    ) -> "MemoryStore":
        self._field.append(
            (
                normalize_role(role),
                FieldGrantData(
                    name=name,
                    key=key,
                    allowed_fields=frozenset(allowed_fields),
                    allowed_values={
                        f: frozenset(v) for f, v in (allowed_values or {}).items()
                    },
                ),
            )
        )
        return self

    # -- GrantStore -------------------------------------------------------

    def has_endpoint_grant(self, roles: Sequence[str], key: str) -> bool:
        return any((r, key) in self._endpoint for r in roles)

    def object_grants(self, roles: Sequence[str], key: str) -> list[ObjectGrantData]:
        seen: dict[str, ObjectGrantData] = {}
        for role, grant in self._object:
            if role in roles and grant.key == key:
                seen[grant.name] = grant
        return [seen[n] for n in sorted(seen)]

    def field_grants(self, roles: Sequence[str], key: str) -> list[FieldGrantData]:
        seen: dict[str, FieldGrantData] = {}
        for role, grant in self._field:
            if role in roles and grant.key == key:
                seen[grant.name] = grant
        return [seen[n] for n in sorted(seen)]


def seed_database_from(store: MemoryStore, *, prefix: str = "seeded") -> None:
    """Persist a MemoryStore's grants as composed permissions.

    One permission per role, which is the flat shape a fixture describes.  It
    is not how a human would compose them — the point of the abstract role is
    that several roles share one permission — but for seeding a demo or a test
    it is the honest translation of "this role may do these things".

    Every reference is a foreign key now, so a fixture naming a filter that
    code does not declare fails here rather than resolving to nothing at
    request time.
    """
    from .catalogue.models import (
        RegisteredEndpoint,
        RegisteredFieldGroup,
        RegisteredFilter,
        RegisteredObject,
    )

    def permission_for(role_key: str) -> Permission:
        role, _ = Role.objects.get_or_create(
            key=normalize_role(role_key), defaults={"label": role_key}
        )
        permission, _ = Permission.objects.get_or_create(
            key=f"{prefix}-{role.key}",
            defaults={"name": f"{role_key} (seeded)"},
        )
        RolePermission.objects.get_or_create(role=role, permission=permission)
        return permission

    for role_key, key in sorted(store._endpoint):
        PermissionEndpoint.objects.get_or_create(
            permission=permission_for(role_key),
            endpoint=RegisteredEndpoint.objects.get(key=key),
        )

    for role_key, data in store._object:
        object_key, _, endpoint_key = data.key.rpartition(".")
        permission = permission_for(role_key)
        rule, _ = PermissionRule.objects.get_or_create(
            permission=permission,
            object=RegisteredObject.objects.get(key=object_key),
            endpoint_key=endpoint_key,
            label=data.name,
            defaults={"order": permission.rules.count()},
        )
        for order, (condition_id, params) in enumerate(data.conditions):
            PermissionRuleCondition.objects.get_or_create(
                rule=rule,
                filter=RegisteredFilter.objects.get(key=condition_id),
                defaults={"params": dict(params), "order": order},
            )

    for role_key, data in store._field:
        object_key, _, endpoint_key = data.key.rpartition(".")
        # A fixture lists fields; the catalogue grants groups. Every group
        # wholly contained in the fixture's allow-list is granted — a group
        # only partly listed is not, because granting it would hand out a
        # field the fixture did not ask for.
        groups = RegisteredFieldGroup.objects.filter(object__key=object_key)
        for group in groups:
            if not set(group.fields or ()) <= data.allowed_fields:
                continue
            PermissionFieldGrant.objects.get_or_create(
                permission=permission_for(role_key),
                field_group=group,
                endpoint_key=endpoint_key,
                defaults={
                    "allowed_values": {
                        f: sorted(v)
                        for f, v in data.allowed_values.items()
                        if f in (group.fields or ())
                    }
                },
            )
