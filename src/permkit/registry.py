"""The code-side registry of keys and object blocks.

Registration is what makes the system fail *closed* and *loudly*.  A key that
was never registered is denied and raises on lookup, rather than resolving to
a silent ``False`` the way a plain dict lookup would — which is how a typo in
config becomes an invisible denial nobody notices for months.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .base import (
    ActionSpec,
    ObjectSpec,
    BlockSpec,
    FieldGroupSpec,
    KeySpec,
    Mode,
    ObjectBlock,
    Param,
    ScopePointSpec,
)
from .exceptions import DuplicateRegistration, UnknownBlock, UnknownKey


class Registry:
    def __init__(self) -> None:
        self._objects: dict[str, ObjectSpec] = {}
        self._keys: dict[str, KeySpec] = {}
        self._blocks: dict[str, BlockSpec] = {}
        self._actions: dict[str, ActionSpec] = {}
        self._field_groups: dict[tuple[str, str], FieldGroupSpec] = {}
        # A list per (object, action): several sites may legitimately scope the
        # same pair — a list view and an export, say.
        self._scope_points: dict[tuple[str, str], list[ScopePointSpec]] = {}

    # -- registration -----------------------------------------------------

    def register_key(
        self,
        id: str,
        *,
        resource: str,
        mode: Mode,
        model: type | None = None,
        fields: Iterable[str] = (),
        scopable: bool = True,
        fk_scopes: Mapping[str, str] | None = None,
    ) -> KeySpec:
        if id in self._keys:
            raise DuplicateRegistration(f"Key {id!r} is already registered.")
        spec = KeySpec(
            id=id,
            resource=resource,
            mode=Mode(mode),
            model=model,
            fields=frozenset(fields),
            scopable=scopable,
            fk_scopes=dict(fk_scopes or {}),
        )
        self._keys[id] = spec
        return spec

    def register_block(
        self,
        id: str,
        *,
        params: Mapping[str, Param] | None = None,
        multi_valued: bool = False,
        object_key: str | None = None,
    ):
        def decorator(cls: type[ObjectBlock]) -> type[ObjectBlock]:
            if id in self._blocks:
                raise DuplicateRegistration(
                    f"Block {id!r} is already registered."
                )
            if not issubclass(cls, ObjectBlock):
                raise TypeError(
                    f"{cls.__name__} must subclass ObjectBlock to be registered."
                )
            self._blocks[id] = BlockSpec(
                id=id,
                cls=cls,
                params=dict(params or {}),
                multi_valued=multi_valued,
                object_key=object_key,
            )
            return cls

        return decorator

    # -- catalogue registrations ------------------------------------------

    def register_object(
        self, key: str, *, model: type | None = None, label: str = ""
    ) -> ObjectSpec:
        existing = self._objects.get(key)
        spec = ObjectSpec(
            key=key,
            model=model or (existing.model if existing else None),
            label=label or (existing.label if existing else key.capitalize()),
            references=dict(existing.references) if existing else {},
        )
        if existing and model and existing.model and existing.model is not model:
            raise DuplicateRegistration(
                f"Object {key!r} is already bound to {existing.model.__name__}; "
                f"cannot rebind to {model.__name__}."
            )
        self._objects[key] = spec
        return spec

    def register_references(
        self, object_key: str, references: Mapping[str, str]
    ) -> None:
        """Record which of an object's foreign keys are governed by which key."""
        existing = self._objects.get(object_key) or self.register_object(object_key)
        merged = {**existing.references, **dict(references)}
        self._objects[object_key] = ObjectSpec(
            key=existing.key,
            model=existing.model,
            label=existing.label,
            references=merged,
        )

    def object(self, key: str) -> ObjectSpec:
        try:
            return self._objects[key]
        except KeyError:
            raise UnknownKey(key) from None

    def has_object(self, key: str) -> bool:
        return key in self._objects


    def register_action(
        self, key: str, *, label: str = "", mode: str = "READ", target: str = ""
    ) -> ActionSpec:
        """Declare an action, or add another component that enforces it."""
        existing = self._actions.get(key)
        if existing is None:
            # Mode falls back to the same action-name convention the derived
            # KeySpec uses, so a plain re-declaration need not restate it.
            resolved = Mode(mode) if mode else (
                Mode.READ
                if key.rpartition(".")[2] in self.READ_ACTIONS
                else Mode.WRITE
            )
            spec = ActionSpec(
                key=key,
                label=label or key,
                mode=resolved,
                targets=(target,) if target else (),
            )
            self._actions[key] = spec
            return spec

        if label and label != existing.label:
            raise DuplicateRegistration(
                f"Action {key!r} is already labelled {existing.label!r}; "
                f"cannot relabel to {label!r}."
            )
        if mode and Mode(mode) != existing.mode:
            raise DuplicateRegistration(
                f"Action {key!r} is already {existing.mode.value}; "
                f"cannot redeclare as {mode}."
            )
        merged = ActionSpec(
            key=existing.key,
            label=existing.label,
            mode=existing.mode,
            targets=tuple(dict.fromkeys((*existing.targets, target))) if target
            else existing.targets,
        )
        self._actions[key] = merged
        return merged

    def register_field_group(
        self,
        object_key: str,
        group_key: str,
        *,
        fields: Iterable[str],
        label: str = "",
    ) -> FieldGroupSpec:
        ident = (object_key, group_key)
        spec = FieldGroupSpec(
            object_key=object_key,
            key=group_key,
            fields=tuple(fields),
            label=label or group_key.replace("_", " ").capitalize(),
        )
        existing = self._field_groups.get(ident)
        if existing is not None and existing != spec:
            raise DuplicateRegistration(
                f"Field group {object_key}.{group_key} is already registered "
                f"with different fields: {sorted(existing.fields)} vs "
                f"{sorted(spec.fields)}."
            )
        self._field_groups[ident] = spec
        return spec

    def register_scope_point(
        self, object_key: str, action_key: str, *, target: str = ""
    ) -> ScopePointSpec:
        spec = ScopePointSpec(
            object_key=object_key, action_key=action_key, target=target
        )
        self._scope_points.setdefault((object_key, action_key), []).append(spec)
        return spec

    # -- lookup -----------------------------------------------------------

    #: Actions treated as reads when no ``@api_action`` declares otherwise.
    READ_ACTIONS = frozenset({"view", "list", "detail", "read", "export"})
    #: Actions with no row yet, so nothing to scope.
    CREATE_ACTIONS = frozenset({"create", "add"})

    def known_keys(self) -> set[str]:
        """Every key some declaration mentions.

        Keys are assembled rather than declared, so without this any
        ``widget.<anything>`` would resolve and a typo would silently become a
        valid key that nobody granted.
        """
        known = set(self._keys) | set(self._actions)
        known |= {f"{obj}.{action}" for obj, action in self._scope_points}
        for spec in self._objects.values():
            # A key named only as a governed reference is still a real key.
            known |= set(spec.references.values())
        return known

    def key(self, id: str) -> KeySpec:
        """Assemble a key from the component declarations that mention it.

        Nothing declares a key as a whole any more: the object binds the model,
        the selector says whether it is scopable, the serializer contributes the
        controlled fields and governed references, and ``@api_action`` supplies
        the mode.  Explicitly registered keys still win, for the rare case that
        needs one.
        """
        if id in self._keys:
            return self._keys[id]

        object_key, _, action = id.rpartition(".")
        obj = self._objects.get(object_key)
        if obj is None or not action or id not in self.known_keys():
            raise UnknownKey(id)

        declared = self._actions.get(id)
        fields = frozenset(
            name
            for group in self.field_groups_for(object_key).values()
            for name in group.fields
        )
        return KeySpec(
            id=id,
            resource=object_key,
            mode=(
                declared.mode
                if declared
                else (Mode.READ if action in self.READ_ACTIONS else Mode.WRITE)
            ),
            model=obj.model,
            fields=fields,
            # "Has rows to scope" — a different question from "some selector
            # applies filters here", which is the coverage check.
            scopable=obj.model is not None and action not in self.CREATE_ACTIONS,
            fk_scopes=dict(obj.references),
        )

    def block(self, id: str) -> BlockSpec:
        try:
            return self._blocks[id]
        except KeyError:
            raise UnknownBlock(id) from None

    def has_key(self, id: str) -> bool:
        return id in self.known_keys()

    def has_block(self, id: str) -> bool:
        return id in self._blocks

    @property
    def keys(self) -> dict[str, KeySpec]:
        return dict(self._keys)

    @property
    def blocks(self) -> dict[str, BlockSpec]:
        return dict(self._blocks)

    @property
    def actions(self) -> dict[str, ActionSpec]:
        return dict(self._actions)

    @property
    def field_groups(self) -> dict[tuple[str, str], FieldGroupSpec]:
        return dict(self._field_groups)

    @property
    def scope_points(self) -> dict[tuple[str, str], list[ScopePointSpec]]:
        return {k: list(v) for k, v in self._scope_points.items()}

    # -- catalogue queries ------------------------------------------------

    def filters_for(self, object_key: str) -> dict[str, BlockSpec]:
        return {
            spec.id: spec
            for spec in self._blocks.values()
            if spec.object_key == object_key
        }

    def field_groups_for(self, object_key: str) -> dict[str, FieldGroupSpec]:
        return {
            key: spec
            for (obj, key), spec in self._field_groups.items()
            if obj == object_key
        }

    def has_scope_point(self, object_key: str) -> bool:
        """True when some site actually applies this object's filters.

        The catalogue check behind problem "a filter is configured but never
        fires": filters on an object with no scope point cannot take effect
        anywhere, however carefully an administrator composes them.
        """
        return any(obj == object_key for obj, _ in self._scope_points)

    # -- maintenance ------------------------------------------------------

    def clear(self) -> None:
        """Drop all registrations. Test helper; never call at runtime."""
        self._objects.clear()
        self._keys.clear()
        self._blocks.clear()
        self._actions.clear()
        self._field_groups.clear()
        self._scope_points.clear()


registry = Registry()


def register_key(id: str, **kwargs: Any) -> KeySpec:
    return registry.register_key(id, **kwargs)


def register_block(id: str, **kwargs: Any):
    return registry.register_block(id, **kwargs)
