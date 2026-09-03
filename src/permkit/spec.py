"""Permissions as a file you can review, diff and re-run.

The admin is where permissions are *composed*; this is how a baseline set gets
into an environment that has no one sitting in front of it — a fresh database,
CI, a colleague's laptop, a deploy that must not wait for someone to click.

A spec is an ordinary Python module holding two dicts::

    PERMISSIONS = {
        "widget-browse-own-warehouse": {
            "name": "Browse my warehouse's widgets",
            "endpoints": ["widget.view"],
            "rules": [
                {
                    "key": "widget.view",
                    "label": "in my warehouse",
                    "conditions": [{"filter": "widget.warehouse"}],
                }
            ],
        },
    }

    ROLES = {
        "w_keeper": {
            "label": "Warehouse keeper",
            "permissions": ["widget-browse-own-warehouse"],
        },
    }

Two rules govern what applying it does, and they are different on purpose:

* **A permission named in the spec is fully managed.** Its endpoints, rules
  and field grants are made to match the file exactly, so deleting a condition
  from the file deletes it from the database. Anything else about it would
  make the file a lie.
* **Role bindings are only ever added.** A deploy re-running this must not
  revoke a permission someone granted in the admin an hour ago. Removing one
  is a deliberate act, and belongs in the admin where it is audited.

Permissions the spec does not name are never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from django.db import transaction

from .catalogue.models import (
    RegisteredEndpoint,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
)
from .exceptions import ConfigurationError
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
from .registry import registry


@dataclass
class SpecReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    bindings: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.roles or self.bindings)


# -- validation -----------------------------------------------------------


def _catalogue_or_fail(model, label: str, **lookup):
    """Resolve a catalogue row, or say precisely what is missing and why.

    A spec references the catalogue by key, and the catalogue is generated. So
    a miss means one of two very different things — the declaration is absent
    from the code, or it is present and nobody has run ``permkit_sync`` — and
    the fix differs. Saying which saves the reader that guess.
    """
    row = model.objects.filter(**lookup).first()
    if row is None:
        if not model.objects.exists():
            raise ConfigurationError(
                f"The catalogue holds no {label} at all. Run permkit_sync "
                f"before applying a spec — composition points at the "
                f"catalogue by foreign key, so there is nothing to point at."
            )
        raise ConfigurationError(
            f"No {label} matching {lookup!r}. Either no component declares it, "
            f"or the declaration is newer than the last permkit_sync run."
        )
    if not row.is_live:
        raise ConfigurationError(
            f"The {label} {lookup!r} is retired — it was declared once and the "
            f"code no longer declares it. Granting it would compose a rule "
            f"nothing honours."
        )
    return row


def _split(key: str, what: str) -> tuple[str, str]:
    object_key, sep, verb = key.rpartition(".")
    if not sep or not object_key or not verb:
        raise ConfigurationError(
            f"{what} {key!r} must be written as 'object.endpoint', "
            f"e.g. 'widget.view'."
        )
    return object_key, verb


# -- applying -------------------------------------------------------------


def _apply_endpoints(permission: Permission, keys) -> bool:
    wanted = {}
    for key in keys or ():
        wanted[key] = _catalogue_or_fail(RegisteredEndpoint, "endpoint", key=key)

    existing = {e.endpoint.key: e for e in permission.endpoints.select_related("endpoint")}
    changed = False
    for key, row in wanted.items():
        if key not in existing:
            PermissionEndpoint.objects.create(permission=permission, endpoint=row)
            changed = True
    for key, row in existing.items():
        if key not in wanted:
            row.delete()
            changed = True
    return changed


def _apply_rules(permission: Permission, rules) -> bool:
    changed = False
    seen: set[tuple] = set()

    for spec in rules or ():
        key = spec["key"]
        object_key, verb = _split(key, "Rule key")
        obj = _catalogue_or_fail(RegisteredObject, "object", key=object_key)
        label = spec.get("label", "")

        # Identity is the label within a key, so a permission may hold two
        # rules for one key — which is the union that widens access — as long
        # as each says what it is for.
        ident = (obj.pk, verb, label)
        if ident in seen:
            raise ConfigurationError(
                f"Permission {permission.key!r} has two rules for {key!r} "
                f"labelled {label!r}. Give them different labels: the label is "
                f"what distinguishes one rule from another when re-applying."
            )
        seen.add(ident)

        rule, created = PermissionRule.objects.get_or_create(
            permission=permission, object=obj, endpoint_key=verb, label=label,
            defaults={"order": len(seen) - 1},
        )
        changed = changed or created

        wanted = {}
        for entry in spec.get("conditions") or ():
            filter_key = entry["filter"] if isinstance(entry, Mapping) else entry
            params = entry.get("params", {}) if isinstance(entry, Mapping) else {}
            row = _catalogue_or_fail(RegisteredFilter, "filter", key=filter_key)
            if row.object_id != obj.pk:
                raise ConfigurationError(
                    f"Filter {filter_key!r} is declared for "
                    f"{row.object.key!r}, but rule {key!r} is about "
                    f"{object_key!r}. It would filter on the wrong column."
                )
            if registry.has_condition(filter_key):
                registry.condition(filter_key).bind(params)  # raises InvalidParams
            wanted[filter_key] = params

        existing = {c.filter.key: c for c in rule.conditions.select_related("filter")}
        for order, (filter_key, params) in enumerate(wanted.items()):
            current = existing.get(filter_key)
            if current is None:
                PermissionRuleCondition.objects.create(
                    rule=rule,
                    filter=RegisteredFilter.objects.get(key=filter_key),
                    params=params,
                    order=order,
                )
                changed = True
            elif current.params != params:
                current.params = params
                current.save(update_fields=["params"])
                changed = True
        for filter_key, current in existing.items():
            if filter_key not in wanted:
                current.delete()
                changed = True

    for rule in permission.rules.select_related("object"):
        if (rule.object_id, rule.endpoint_key, rule.label) not in seen:
            rule.delete()
            changed = True
    return changed


def _apply_fields(permission: Permission, grants) -> bool:
    changed = False
    seen: set[tuple] = set()

    for spec in grants or ():
        group_key = spec["group"] if isinstance(spec, Mapping) else spec
        object_key, group_name = _split(group_key, "Field group")
        verb = spec.get("endpoint") if isinstance(spec, Mapping) else None
        if not verb:
            raise ConfigurationError(
                f"Field grant {group_key!r} needs an 'endpoint', e.g. 'view' or "
                f"'update'. Reading and writing a field are separate grants."
            )
        group = _catalogue_or_fail(
            RegisteredFieldGroup, "field group",
            object__key=object_key, key=group_name,
        )
        values = spec.get("values", {}) if isinstance(spec, Mapping) else {}
        seen.add((group.pk, verb))

        grant, created = PermissionFieldGrant.objects.get_or_create(
            permission=permission, field_group=group, endpoint_key=verb,
            defaults={"allowed_values": values},
        )
        if created:
            changed = True
        elif (grant.allowed_values or {}) != values:
            grant.allowed_values = values
            grant.save(update_fields=["allowed_values"])
            changed = True

    for grant in permission.field_grants.all():
        if (grant.field_group_id, grant.endpoint_key) not in seen:
            grant.delete()
            changed = True
    return changed


@transaction.atomic
def apply_spec(spec: Any, *, dry_run: bool = False) -> SpecReport:
    """Make the database match ``spec``, and report what moved.

    Atomic, and validation runs as part of the write — so a spec naming one
    filter that does not exist leaves nothing behind, rather than half a role.
    """
    permissions = dict(getattr(spec, "PERMISSIONS", None) or {})
    roles = dict(getattr(spec, "ROLES", None) or {})
    if not permissions and not roles:
        raise ConfigurationError(
            "Spec defines neither PERMISSIONS nor ROLES. Both are dicts keyed "
            "by slug; see permkit.spec for the shape."
        )

    report = SpecReport()

    for key, body in permissions.items():
        permission, created = Permission.objects.get_or_create(
            key=key,
            defaults={"name": body.get("name", key), "description": body.get("description", "")},
        )
        touched = created
        for attr, value in (("name", body.get("name", key)),
                            ("description", body.get("description", ""))):
            if getattr(permission, attr) != value:
                setattr(permission, attr, value)
                touched = True
        if touched and not created:
            permission.save(update_fields=["name", "description"])

        touched |= _apply_endpoints(permission, body.get("endpoints"))
        touched |= _apply_rules(permission, body.get("rules"))
        touched |= _apply_fields(permission, body.get("fields"))

        (report.created if created else report.updated if touched else report.unchanged).append(key)

    for role_key, body in roles.items():
        role, created = Role.objects.get_or_create(
            key=normalize_role(role_key),
            defaults={"label": body.get("label", role_key), "description": body.get("description", "")},
        )
        if created:
            report.roles.append(role.key)
        for permission_key in body.get("permissions") or ():
            permission = Permission.objects.filter(key=permission_key).first()
            if permission is None:
                raise ConfigurationError(
                    f"Role {role_key!r} is given permission {permission_key!r}, "
                    f"which neither this spec nor the database defines."
                )
            # Added, never removed: a deploy must not revoke what an
            # administrator granted in the admin an hour ago.
            _, made = RolePermission.objects.get_or_create(
                role=role, permission=permission
            )
            report.bindings += 1 if made else 0

    if dry_run:
        transaction.set_rollback(True)
    return report
