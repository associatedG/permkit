"""Publish the registry to the catalogue tables, then check it hangs together.

Three steps, in this order and for these reasons:

1. **Load.**  Force every declaration module in, so the registry being scraped
   is the whole registry and not whichever part some earlier import happened to
   pull in.  See :mod:`permkit.catalogue.loading`.
2. **Upsert, never delete.**  A declaration that has gone from the code has its
   row marked ``is_live=False`` rather than removed, so a composition that
   points at it keeps resolving while showing up as broken.
3. **Validate.**  Report the ways code and configuration have drifted apart.
   Errors fail the run; warnings are printed and do not.

Validation happens *after* the write on purpose.  The failures worth catching
are the ones where configuration references something code no longer declares,
and the row that lets an admin find that configuration is the dead catalogue
row this run just marked.  Rolling the write back on a failed validation would
delete the evidence needed to fix it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from django.core.exceptions import FieldDoesNotExist
from django.db import models, transaction
from django.utils import timezone

from ..models import (
    PermissionEndpoint,
    PermissionFieldGrant,
    PermissionRule,
    PermissionRuleCondition,
)
from ..registry import Registry, registry as default_registry
from .loading import LoadReport, load_declarations
from .models import (
    RegisteredEndpoint,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
    RegisteredScopePoint,
)

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """One way the catalogue, the code and the configuration disagree.

    ``code`` is stable and greppable so a CI job can whitelist a known one
    without pattern-matching on prose.
    """

    code: str
    message: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass
class TableReport:
    name: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    #: Was dead, is declared again — a revert, or a module that failed to load
    #: on the previous run.  Worth its own count: a revived row is the shape a
    #: bad sync leaves behind, so a nonzero number here on a normal deploy is
    #: a hint the last run scraped a thin registry.
    revived: int = 0
    retired: int = 0

    @property
    def live(self) -> int:
        return self.created + self.updated + self.unchanged + self.revived

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.revived or self.retired)


@dataclass
class SyncReport:
    load: LoadReport
    tables: list[TableReport] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == ERROR]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == WARNING]

    @property
    def changed(self) -> bool:
        return any(t.changed for t in self.tables)


# -- writing --------------------------------------------------------------


def _comparable(value: Any) -> Any:
    """Value in the form the column stores it, so instances compare by pk."""
    return value.pk if isinstance(value, models.Model) else value


def _current(row: models.Model, model: type[models.Model], name: str) -> Any:
    """What the row holds now — the raw id for a foreign key, so comparing
    against a desired value never costs a query."""
    fld = model._meta.get_field(name)
    return getattr(row, f"{name}_id") if fld.many_to_one else getattr(row, name)


def _natural(values: Mapping[str, Any], names: Sequence[str]) -> tuple:
    return tuple(_comparable(values[n]) for n in names)


def _natural_of_row(row: models.Model, names: Sequence[str], model) -> tuple:
    return tuple(_current(row, model, n) for n in names)


def _upsert(
    model: type[models.Model],
    desired: Iterable[Mapping[str, Any]],
    *,
    natural: Sequence[str],
    now,
    name: str,
) -> tuple[TableReport, dict[tuple, models.Model]]:
    """Reconcile one table against what the registry declares.

    Reads the whole table up front and diffs in Python.  The catalogue is
    small by construction — it has one row per *declaration*, not per row of
    domain data — and an honest created/updated/unchanged count is worth more
    here than a blind ``update_or_create``: a deploy that reports twelve
    updates when nothing changed teaches people to ignore the output.
    """
    report = TableReport(name)
    existing = {_natural_of_row(r, natural, model): r for r in model.objects.all()}
    live_rows: dict[tuple, models.Model] = {}

    for values in desired:
        ident = _natural(values, natural)
        if ident in live_rows:
            continue  # the same declaration reached us twice; one row either way
        row = existing.get(ident)
        if row is None:
            row = model.objects.create(**values, last_seen_at=now, is_live=True)
            report.created += 1
            live_rows[ident] = row
            continue

        drifted = [
            f
            for f, v in values.items()
            if _current(row, model, f) != _comparable(v)
        ]
        for f in drifted:
            setattr(row, f, values[f])
        if not row.is_live:
            report.revived += 1
        elif drifted:
            report.updated += 1
        else:
            report.unchanged += 1
        row.is_live = True
        row.last_seen_at = now
        row.save(update_fields=[*drifted, "is_live", "last_seen_at"])
        live_rows[ident] = row

    stale = [r.pk for ident, r in existing.items() if ident not in live_rows and r.is_live]
    if stale:
        report.retired = model.objects.filter(pk__in=stale).update(is_live=False)

    return report, live_rows


# -- scraping the registry -------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Params are config, so they must survive a round trip through JSON."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def _open_params(spec) -> dict:
    out = {}
    for name, param in spec.params.items():
        entry = {"type": param.type.__name__, "required": param.required}
        if not param.required:
            entry["default"] = _jsonable(param.default)
        out[name] = entry
    return out


def _object_keys(registry: Registry) -> set[str]:
    """Every object key anything mentions, not only those bound to a model.

    A selector may scope ``widget`` and a serializer may group its fields
    without either of them calling ``permission_object``.  Those keys still
    need a catalogue row, or their filters and groups have no parent to hang
    from — which is why the unbound case is a warning here rather than a
    missing row.
    """
    keys = set(registry.objects)
    keys |= {
        s.object_key for s in registry.conditions.values() if s.object_key is not None
    }
    keys |= {obj for obj, _ in registry.field_groups}
    keys |= {obj for obj, _ in registry.scope_points}
    return keys


def _object_rows(registry: Registry) -> list[dict]:
    rows = []
    for key in sorted(_object_keys(registry)):
        spec = registry.objects.get(key)
        model = getattr(spec, "model", None)
        rows.append(
            {
                "key": key,
                "label": getattr(spec, "label", "") or key.capitalize(),
                "model_label": model._meta.label if model is not None else "",
            }
        )
    return rows


def _filter_rows(registry: Registry, objects: dict[str, RegisteredObject]) -> list[dict]:
    rows = []
    for spec in registry.conditions.values():
        if spec.object_key is None:
            continue  # unattachable; reported as a warning by validate()
        rows.append(
            {
                "object": objects[spec.object_key],
                "key": spec.id,
                "label": (spec.cls.__doc__ or "").strip().splitlines()[0].strip()
                if spec.cls.__doc__
                else spec.id,
                "open_params": _open_params(spec),
                "multi_valued": spec.multi_valued,
            }
        )
    return rows


def _endpoint_rows(registry: Registry) -> list[dict]:
    return [
        {"key": spec.key, "label": spec.label, "targets": list(spec.targets)}
        for spec in registry.endpoints.values()
    ]


def _field_group_rows(
    registry: Registry, objects: dict[str, RegisteredObject]
) -> list[dict]:
    return [
        {
            "object": objects[spec.object_key],
            "key": spec.key,
            "label": spec.label,
            "fields": list(spec.fields),
        }
        for spec in registry.field_groups.values()
    ]


def _scope_point_rows(
    registry: Registry, objects: dict[str, RegisteredObject]
) -> list[dict]:
    return [
        {
            "object": objects[spec.object_key],
            "endpoint_key": spec.endpoint_key,
            "target": spec.target,
            "label": f"{spec.object_key}.{spec.endpoint_key}",
        }
        for specs in registry.scope_points.values()
        for spec in specs
    ]


# -- validation ------------------------------------------------------------


def _model_has(model: type[models.Model], name: str) -> bool:
    """Whether ``name`` is something this model can actually produce.

    A concrete column, or an attribute on the class.  The second case is not
    laxness: a computed property is serialized like any other field and the
    field tier strips it from the payload the same way, so refusing to let a
    group name one would be wrong.  What this still catches is the case worth
    catching — a typo, or a column that has been renamed out from under a
    grant that is still handing it out.
    """
    try:
        model._meta.get_field(name)
        return True
    except FieldDoesNotExist:
        return hasattr(model, name)


def _validate_declarations(registry: Registry) -> list[Problem]:
    problems: list[Problem] = []

    for key in sorted(_object_keys(registry)):
        spec = registry.objects.get(key)
        if spec is None or spec.model is None:
            problems.append(
                Problem(
                    "unbound-object",
                    f"Object {key!r} is referenced by filters, field groups or "
                    f"scope points but never bound to a model. Add "
                    f"permission_object({key!r}, model=...) beside its filters, "
                    f"or its field groups cannot be checked against anything.",
                    WARNING,
                )
            )

    for spec in registry.conditions.values():
        if spec.object_key is None:
            problems.append(
                Problem(
                    "unattached-filter",
                    f"Filter {spec.id!r} is not declared for any object, so it "
                    f"cannot be catalogued and nothing stops it being composed "
                    f"onto the wrong object.",
                    WARNING,
                )
            )

    # Filters on an object nothing ever scopes can be composed and will never
    # fire.  An administrator has no way to see that from the admin, so it has
    # to be the build that says so.
    for key in sorted(_object_keys(registry)):
        if not registry.conditions_for(key):
            continue
        if not registry.has_scope_point(key):
            names = sorted(registry.conditions_for(key))
            problems.append(
                Problem(
                    "filters-never-fire",
                    f"Object {key!r} declares {len(names)} filter(s) "
                    f"({', '.join(names)}) but no selector applies them. "
                    f"Decorate a selector with @object_permissions({key!r}, ...) "
                    f"and call apply_permissions(), or the rules an admin "
                    f"composes from these filters will silently do nothing.",
                )
            )

    for (object_key, group_key), spec in sorted(registry.field_groups.items()):
        obj = registry.objects.get(object_key)
        if obj is None or obj.model is None:
            continue  # already reported as unbound-object
        missing = [f for f in spec.fields if not _model_has(obj.model, f)]
        if missing:
            problems.append(
                Problem(
                    "unknown-field",
                    f"Field group {object_key}.{group_key} names "
                    f"{', '.join(repr(m) for m in missing)}, which "
                    f"{obj.model._meta.label} does not have. A grant on this "
                    f"group hands out a field that is not there.",
                )
            )

    return problems


def _validate_compositions(registry: Registry) -> list[Problem]:
    """Check the composed permissions still point at things the code declares.

    Foreign keys already make half of the old failures unrepresentable: a rule
    cannot name a filter that never existed, and the admin narrows the filter
    dropdown to the object the rule is about. What is left is what a foreign
    key cannot express — a row that still exists but is no longer *live*,
    because the declaration behind it has left the code — plus the object
    mismatch, which is a legal foreign key and a wrong rule.
    """
    problems: list[Problem] = []
    known = registry.known_keys()

    dead_endpoints = PermissionEndpoint.objects.filter(
        endpoint__is_live=False
    ).select_related("permission", "endpoint")
    for entry in dead_endpoints:
        problems.append(
            Problem(
                "stale-endpoint",
                f"Permission {entry.permission.key!r} grants endpoint "
                f"{entry.endpoint.key!r}, which no component declares any more. "
                f"Nobody can exercise it.",
            )
        )

    rules = PermissionRule.objects.select_related("permission", "object")
    for rule in rules:
        if not rule.object.is_live:
            problems.append(
                Problem(
                    "stale-object",
                    f"Rule {rule.name} is about object {rule.object.key!r}, which "
                    f"no declaration mentions any more.",
                )
            )
        elif rule.key not in known:
            problems.append(
                Problem(
                    "stale-key",
                    f"Rule {rule.name} is for key {rule.key!r}, which no "
                    f"declaration mentions. It can never match.",
                )
            )

    conditions = PermissionRuleCondition.objects.select_related(
        "filter", "filter__object", "rule", "rule__object", "rule__permission"
    )
    for condition in conditions:
        if not condition.filter.is_live:
            problems.append(
                Problem(
                    "stale-filter",
                    f"Rule {condition.rule.name} uses filter "
                    f"{condition.filter.key!r}, which is no longer declared in "
                    f"code. The rule still resolves as it did, but the filter "
                    f"cannot be edited or reasoned about.",
                )
            )
        if condition.filter.object_id != condition.rule.object_id:
            problems.append(
                Problem(
                    "misfiled-filter",
                    f"Rule {condition.rule.name} applies filter "
                    f"{condition.filter.key!r} (declared for "
                    f"{condition.filter.object.key!r}) to a rule about "
                    f"{condition.rule.object.key!r}. It would filter on the "
                    f"wrong column at request time.",
                )
            )

    field_grants = PermissionFieldGrant.objects.select_related(
        "permission", "field_group", "field_group__object"
    )
    for grant in field_grants:
        if not grant.field_group.is_live:
            problems.append(
                Problem(
                    "stale-group",
                    f"Permission {grant.permission.key!r} grants field group "
                    f"{grant.field_group.object.key}.{grant.field_group.key}, "
                    f"which no serializer declares any more.",
                )
            )
        elif grant.key not in known:
            problems.append(
                Problem(
                    "stale-key",
                    f"Permission {grant.permission.key!r} grants fields for key "
                    f"{grant.key!r}, which no declaration mentions.",
                )
            )

    return problems


def validate(registry: Registry | None = None) -> list[Problem]:
    """Every disagreement between code, catalogue and configuration."""
    registry = registry or default_registry
    return _validate_declarations(registry) + _validate_compositions(registry)


# -- the whole run ---------------------------------------------------------


@transaction.atomic
def sync_catalogue(
    *,
    registry: Registry | None = None,
    load: bool = True,
    urlconf: bool = True,
) -> SyncReport:
    """Publish the registry to the catalogue tables and validate the result.

    Idempotent: a second run against an unchanged registry creates, updates,
    revives and retires nothing.  ``last_seen_at`` still moves, because that is
    what it is for.
    """
    registry = registry or default_registry
    load_report = (
        load_declarations(urlconf=urlconf) if load else LoadReport()
    )
    now = timezone.now()
    report = SyncReport(load=load_report)

    # Objects first: everything else keys off their rows.
    objects_report, object_rows = _upsert(
        RegisteredObject, _object_rows(registry), natural=("key",), now=now,
        name="objects",
    )
    objects = {r.key: r for r in object_rows.values()}
    report.tables.append(objects_report)

    for model, rows, natural, name in (
        (RegisteredEndpoint, _endpoint_rows(registry), ("key",), "endpoints"),
        (RegisteredFilter, _filter_rows(registry, objects), ("key",), "filters"),
        (
            RegisteredFieldGroup,
            _field_group_rows(registry, objects),
            ("object", "key"),
            "field groups",
        ),
        (
            RegisteredScopePoint,
            _scope_point_rows(registry, objects),
            ("object", "endpoint_key", "target"),
            "scope points",
        ),
    ):
        table_report, _ = _upsert(model, rows, natural=natural, now=now, name=name)
        report.tables.append(table_report)

    report.problems = validate(registry)
    return report
