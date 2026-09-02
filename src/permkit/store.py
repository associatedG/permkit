"""Where grants are read from.

A seam so the same resolver can run against the database (the normal case) or
an in-memory fixture (fast resolver tests, and consumers that would rather
keep rules in version control than in a table).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable

from .models import (
    FieldGrant,
    ObjectGrant,
    RoleEndpointGrant,
    RoleFieldGrant,
    RoleObjectGrant,
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
    """Reads grants from the permkit tables."""

    def has_endpoint_grant(self, roles: Sequence[str], key: str) -> bool:
        if not roles:
            return False
        return RoleEndpointGrant.objects.filter(role__in=roles, key=key).exists()

    def object_grants(self, roles: Sequence[str], key: str) -> list[ObjectGrantData]:
        if not roles:
            return []
        qs = (
            ObjectGrant.objects.filter(key=key, role_bindings__role__in=roles)
            .distinct()
            .order_by("name")
        )
        return [
            ObjectGrantData(
                name=g.name, key=g.key, conditions=_parse_conditions(g.conditions)
            )
            for g in qs
        ]

    def field_grants(self, roles: Sequence[str], key: str) -> list[FieldGrantData]:
        if not roles:
            return []
        qs = (
            FieldGrant.objects.filter(key=key, role_bindings__role__in=roles)
            .distinct()
            .order_by("name")
        )
        return [
            FieldGrantData(
                name=g.name,
                key=g.key,
                allowed_fields=frozenset(g.allowed_fields),
                allowed_values={
                    f: frozenset(v) for f, v in (g.allowed_values or {}).items()
                },
            )
            for g in qs
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


def seed_database_from(store: MemoryStore) -> None:
    """Persist a MemoryStore's grants into the database tables."""
    for role, key in sorted(store._endpoint):
        RoleEndpointGrant.objects.get_or_create(role=role, key=key)
    for role, data in store._object:
        grant, _ = ObjectGrant.objects.update_or_create(
            name=data.name,
            defaults={
                "key": data.key,
                "conditions": [
                    {"condition": b, "params": dict(p)} for b, p in data.conditions
                ],
            },
        )
        RoleObjectGrant.objects.get_or_create(role=role, grant=grant)
    for role, data in store._field:
        grant, _ = FieldGrant.objects.update_or_create(
            name=data.name,
            defaults={
                "key": data.key,
                "allowed_fields": sorted(data.allowed_fields),
                "allowed_values": {
                    f: sorted(v) for f, v in data.allowed_values.items()
                },
            },
        )
        RoleFieldGrant.objects.get_or_create(role=role, grant=grant)
