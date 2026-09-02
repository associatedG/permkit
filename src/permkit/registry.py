"""The code-side registry of keys and object conditions.

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
    ConditionSpec,
    FieldGroupSpec,
    KeySpec,
    ObjectCondition,
    Param,
    ScopePointSpec,
)
from .exceptions import DuplicateRegistration, UnknownCondition, UnknownKey


class Registry:
    def __init__(self) -> None:
        self._objects: dict[str, ObjectSpec] = {}
        self._keys: dict[str, KeySpec] = {}
        self._conditions: dict[str, ConditionSpec] = {}
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
        model: type | None = None,
        fields: Iterable[str] = (),
        fk_scopes: Mapping[str, str] | None = None,
    ) -> KeySpec:
        if id in self._keys:
            raise DuplicateRegistration(f"Key {id!r} is already registered.")
        spec = KeySpec(
            id=id,
            resource=resource,
            model=model,
            fields=frozenset(fields),
            fk_scopes=dict(fk_scopes or {}),
        )
        self._keys[id] = spec
        return spec

    def register_condition(
        self,
        id: str,
        *,
        params: Mapping[str, Param] | None = None,
        multi_valued: bool = False,
        object_key: str | None = None,
    ):
        def decorator(cls: type[ObjectCondition]) -> type[ObjectCondition]:
            if id in self._conditions:
                raise DuplicateRegistration(
                    f"Condition {id!r} is already registered."
                )
            if not issubclass(cls, ObjectCondition):
                raise TypeError(
                    f"{cls.__name__} must subclass ObjectCondition to be registered."
                )
            self._conditions[id] = ConditionSpec(
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
        self, key: str, *, label: str = "", target: str = ""
    ) -> ActionSpec:
        """Declare an action, or add another component that enforces it."""
        existing = self._actions.get(key)
        if existing is None:
            spec = ActionSpec(
                key=key,
                label=label or key,
                targets=(target,) if target else (),
            )
            self._actions[key] = spec
            return spec

        if label and label != existing.label:
            raise DuplicateRegistration(
                f"Action {key!r} is already labelled {existing.label!r}; "
                f"cannot relabel to {label!r}."
            )
        merged = ActionSpec(
            key=existing.key,
            label=existing.label,
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
        the selector says where it is scoped, the serializer contributes the
        controlled fields and governed references, and ``@api_permission`` supplies
        the mode.  Explicitly registered keys still win, for the rare case that
        needs one.
        """
        if id in self._keys:
            return self._keys[id]

        object_key, _, action = id.rpartition(".")
        obj = self._objects.get(object_key)
        if obj is None or not action or id not in self.known_keys():
            raise UnknownKey(id)

        fields = frozenset(
            name
            for group in self.field_groups_for(object_key).values()
            for name in group.fields
        )
        return KeySpec(
            id=id,
            resource=object_key,
            model=obj.model,
            fields=fields,
            fk_scopes=dict(obj.references),
        )

    def condition(self, id: str) -> ConditionSpec:
        try:
            return self._conditions[id]
        except KeyError:
            raise UnknownCondition(id) from None

    def has_key(self, id: str) -> bool:
        return id in self.known_keys()

    def has_condition(self, id: str) -> bool:
        return id in self._conditions

    @property
    def objects(self) -> dict[str, ObjectSpec]:
        return dict(self._objects)

    @property
    def keys(self) -> dict[str, KeySpec]:
        return dict(self._keys)

    @property
    def conditions(self) -> dict[str, ConditionSpec]:
        return dict(self._conditions)

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

    def conditions_for(self, object_key: str) -> dict[str, ConditionSpec]:
        return {
            spec.id: spec
            for spec in self._conditions.values()
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
        self._conditions.clear()
        self._actions.clear()
        self._field_groups.clear()
        self._scope_points.clear()


registry = Registry()


def register_key(id: str, **kwargs: Any) -> KeySpec:
    return registry.register_key(id, **kwargs)


def register_condition(id: str, **kwargs: Any):
    return registry.register_condition(id, **kwargs)
