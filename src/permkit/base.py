"""Core value types: modes, tiers, params, specs and the object-block ABC.

Kept free of the registry itself so ``blocks`` can import these without a
circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from django.db.models import Model, Q

from .exceptions import InvalidParams


class Mode(str, Enum):
    """Whether a key describes reading or mutating."""

    READ = "READ"
    WRITE = "WRITE"


class Tier(str, Enum):
    ENDPOINT = "ENDPOINT"
    OBJECT = "OBJECT"
    FIELD = "FIELD"


_MISSING = object()


@dataclass(frozen=True)
class Param:
    """Declared parameter of an object block.

    Config-time only.  Request-time data (the acting user, their warehouse)
    arrives via :class:`Context`, never through params — keeping the two apart
    stops people putting user data in the grant config.
    """

    type: type
    default: Any = _MISSING

    @property
    def required(self) -> bool:
        return self.default is _MISSING

    def validate(self, block_id: str, name: str, value: Any) -> Any:
        if value is _MISSING:
            if self.required:
                raise InvalidParams(
                    f"Block {block_id!r} requires param {name!r}."
                )
            return self.default
        if not isinstance(value, self.type):
            raise InvalidParams(
                f"Block {block_id!r} param {name!r} expects "
                f"{self.type.__name__}, got {type(value).__name__}."
            )
        return value


@dataclass(frozen=True)
class Context:
    """What an object block may read at request time."""

    user: Any
    extra: Mapping[str, Any] = field(default_factory=dict)


class ObjectBlock:
    """A reusable, parameterised row-level condition.

    ``as_q`` is deliberately the *only* method a block may implement.  An
    object rule must be expressible as a ``Q`` so it can be pushed into the
    SQL ``WHERE`` clause — filtering in Python would silently corrupt
    pagination, ``count()`` and aggregates, not merely slow them down.

    A block that cannot be written as a ``Q`` is a signal to denormalise the
    data, express it as a subquery, or promote it to the endpoint tier.
    """

    def as_q(self, ctx: Context, **params: Any) -> Q:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class BlockSpec:
    id: str
    cls: type[ObjectBlock]
    params: Mapping[str, Param] = field(default_factory=dict)
    #: True when the block traverses a multi-valued relation, so the resolver
    #: knows to apply ``.distinct()`` and avoid duplicate rows.
    multi_valued: bool = False
    #: Which object this filter is about.  Checked against the key's resource
    #: before the filter is applied: without it, a ``crate`` filter composed
    #: onto a ``widget`` action compiles happily and silently filters widgets
    #: on the wrong column.
    object_key: str | None = None

    def bind(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Validate config-supplied params against the declared schema."""
        unknown = set(raw) - set(self.params)
        if unknown:
            raise InvalidParams(
                f"Block {self.id!r} got unknown param(s): {sorted(unknown)}."
            )
        return {
            name: spec.validate(self.id, name, raw.get(name, _MISSING))
            for name, spec in self.params.items()
        }


@dataclass(frozen=True)
class ObjectSpec:
    """A permissioned object — the thing filters and field groups are about.

    Binds an object key to its model.  Everything else about a key (its
    resource, whether it is scopable, which fields it controls, which of its
    references are governed) is derived from the component declarations, so
    this is the only fact that has nowhere else to live.
    """

    key: str
    model: type[Model] | None = None
    label: str = ""
    #: ``{"crate": "crate.view"}`` — contributed by the serializer.
    references: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionSpec:
    """An endpoint action, declared by ``@api_action``.

    Several components may enforce the same action — a list view and a detail
    view are usually one permission — so an action carries every place that
    enforces it rather than a single owner.  An action with no target cannot
    exist, which is what makes a catalogue entry nobody honours unrepresentable
    rather than merely detectable.
    """

    key: str
    label: str
    mode: Mode
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldGroupSpec:
    """A named bundle of fields, declared on the serializer that exposes them.

    Groups rather than bare column names so an admin picks *"Money"* instead of
    ``secret_price`` — and so adding a column to a group updates every abstract
    role that already granted it.
    """

    object_key: str
    key: str
    fields: tuple[str, ...]
    label: str = ""


@dataclass(frozen=True)
class ScopePointSpec:
    """A place where an object's filters are actually applied.

    Recorded so the catalogue can tell that an object's filters have somewhere
    to take effect.  Filters registered for an object with no scope point can
    be configured by an admin and will silently do nothing.
    """

    object_key: str
    action_key: str
    target: str = ""


@dataclass(frozen=True)
class KeySpec:
    """One (resource, action) pair — the unit of "what are you trying to do".

    ``mode`` is what separates read from write: ``order.view`` and
    ``order.update`` are distinct keys carrying distinct grants, so the rows
    you may see and the rows you may edit are configured independently.
    """

    id: str
    resource: str
    mode: Mode
    model: type[Model] | None = None
    #: Fields subject to permission.  Anything *not* listed is unrestricted,
    #: so adopting the field tier does not mean enumerating every column.
    fields: frozenset[str] = frozenset()
    #: False for keys with no row to scope yet, e.g. ``order.create``.
    scopable: bool = True
    #: ``{"material": "material.view"}`` — a foreign key on this resource and
    #: the key that governs which rows it may point at.
    #:
    #: Structural, so it lives in code: *which* key governs the reference is
    #: fixed, while *which rows* that key admits varies per role through its
    #: own object grants.  Without this, a caller who may write the ``material``
    #: field can point it at any material at all — the picker is filtered, the
    #: API is not.
    fk_scopes: Mapping[str, str] = field(default_factory=dict)
