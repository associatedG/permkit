---
name: permkit-grant
description: Create or change permkit permissions and roles from a re-runnable spec file applied with permkit_apply, plus one-off checks through manage.py shell. Use when granting a role access, adding or narrowing a permission, seeding baseline permissions for a new environment or CI, or answering "why can this user do X?" from the command line.
---

# Grant permissions

Two ways in, and picking the right one is most of the job:

| Situation | Use |
|---|---|
| Anything that must exist in another environment, survive a rebuild, or be reviewed | **a spec file** + `permkit_apply` |
| Inspecting, debugging, answering "why?" | **`manage.py shell -c`** |
| A person adjusting access on a live system | **the admin UI** — do not script it |

Never write a throwaway script that creates permissions imperatively. It runs
once, on one database, and nobody can tell later what it did.

## Before anything

Composition points at the catalogue by foreign key, so you can only grant what
the code declares. Look first:

```bash
python manage.py permkit_sync          # publish the current declarations
python manage.py shell -c "
from permkit.catalogue.models import *
print('endpoints   :', list(RegisteredEndpoint.objects.filter(is_live=True).values_list('key', flat=True)))
print('objects     :', list(RegisteredObject.objects.filter(is_live=True).values_list('key', flat=True)))
print('row filters :', list(RegisteredFilter.objects.filter(is_live=True).values_list('key', flat=True)))
print('field groups:', [f'{g.object.key}.{g.key}' for g in RegisteredFieldGroup.objects.filter(is_live=True)])
"
```

If what you need is missing, the fix is in the code — use the
`permkit-declare` skill, not a workaround here.

## The spec file

An ordinary Python module with two dicts. Put it where the project keeps
config-ish code, e.g. `myapp/permissions/baseline.py`, and commit it.

```python
"""Baseline permissions. Applied by permkit_apply on every deploy."""

PERMISSIONS = {
    "widget-browse-all": {
        "name": "Browse every widget",
        "description": "Read access to the whole table. Prices are separate.",
        "endpoints": ["widget.view"],
        "rules": [
            # No conditions means EVERY row. Deliberate, not an empty rule.
            {"key": "widget.view", "label": "every row"},
        ],
    },
    "widget-edit-assigned": {
        "name": "Edit widgets assigned to me",
        "endpoints": ["widget.update"],
        "rules": [
            {
                "key": "widget.update",
                "label": "in my warehouse and assigned to me",
                # TWO conditions on ONE rule -> AND. See the trap below.
                "conditions": [
                    {"filter": "widget.warehouse"},
                    {"filter": "widget.assigned"},
                ],
            },
        ],
    },
    "widget-see-prices": {
        "name": "See widget prices",
        "fields": [{"group": "widget.money", "endpoint": "view"}],
    },
    "widget-draft-only": {
        "name": "Work on drafts",
        "endpoints": ["widget.update"],
        "rules": [{
            "key": "widget.update",
            "label": "drafts",
            "conditions": [
                {"filter": "widget.status_in", "params": {"values": ["DRAFT"]}},
            ],
        }],
        # Constrain the values a field may take, not just whether it is writable.
        "fields": [{
            "group": "widget.status_group", "endpoint": "update",
            "values": {"status": ["DRAFT", "ACTIVE"]},
        }],
    },
}

ROLES = {
    "w_keeper": {
        "label": "Warehouse keeper",
        "description": "Reads their own warehouse; edits only what is assigned.",
        "permissions": ["widget-edit-assigned"],
    },
    "w_admin": {
        "label": "Administrator",
        "permissions": ["widget-browse-all", "widget-see-prices"],
    },
}
```

### Apply it

```bash
python manage.py permkit_apply myapp/permissions/baseline.py
python manage.py permkit_apply myapp.permissions.baseline    # dotted also works
python manage.py permkit_apply <spec> --check                # CI: no writes, non-zero on drift
```

Deploy order is `migrate` → `permkit_sync` → `permkit_apply`. It is idempotent;
re-running an applied spec reports `already applied` and writes nothing.

### What it manages, exactly

- **A permission named in the spec is fully managed.** Endpoints, rules and
  field grants are made to match the file, so deleting a condition from the
  file deletes it from the database. Anything less would make the file a lie.
- **Role bindings are only ever added.** A deploy must not revoke what someone
  granted in the admin an hour ago. Removing a binding is a deliberate act and
  belongs in the admin, where it is visible.
- **Permissions the spec does not name are never touched.**

It refuses, atomically and before writing anything, a spec that names an
endpoint, filter or field group that is missing, retired, or belongs to a
different object — and validates `params` against the filter's declaration.

## The trap: AND versus OR

Rules **union**. Conditions inside a rule **intersect**. The same two filters
arranged two ways give materially different access, and both look right:

```python
# ONE rule, TWO conditions  ->  in my warehouse AND assigned to me   (narrow)
{"key": "widget.update", "label": "mine", "conditions": [
    {"filter": "widget.warehouse"}, {"filter": "widget.assigned"}]}

# TWO rules, ONE each       ->  in my warehouse OR assigned to me    (wide)
{"key": "widget.update", "label": "my warehouse",
 "conditions": [{"filter": "widget.warehouse"}]},
{"key": "widget.update", "label": "assigned to me",
 "conditions": [{"filter": "widget.assigned"}]},
```

More rules can only ever *add* access; more conditions can only ever *remove*
it. Two rules for one key need **different labels** — the label is what tells
them apart when the spec is re-applied.

## Modelling advice

- **A permission is a job, not a person.** "Browse every widget" is one
  permission the admin and the viewer both hold; the admin is distinguished by
  holding more. One-bundle-per-role throws away the whole point.
- **Read and write are separate keys.** `widget.view` and `widget.update` carry
  separate rules, and `widget.update` and `widget.create` are separate too —
  editing a price on an existing row is not setting one on a new row.
- **No role inheritance.** Express it by composition: a manager holds the
  keeper's permission plus an unscoped one, and grants union.
- **A role with no permissions denies everything.** Zero grants resolve to
  DENY, never to an unfiltered queryset.

## Verify — always, before saying you are done

```bash
python manage.py shell -c "
from permkit import explain
from myapp.models import User, Widget
u = User.objects.get(username='some_keeper')
print(explain(u, 'widget.update', Widget.objects.get(pk=1)))
"
```

Prints the roles, the endpoint verdict, every rule with its conditions, whether
the row is in scope, and which controlled fields are withheld. There is also a
preview screen at `/admin/permkit/permission/preview/`.

Counting rows is the other honest check:

```bash
python manage.py shell -c "
from myapp.selectors import widget_list
from myapp.models import User
for u in User.objects.all()[:5]:
    print(f'{u.username:20} {u.role:12} sees {widget_list(fetched_by=u).count()}')
"
```

**A superuser passes every check** while `SUPERUSER_BYPASS` is on, so never
verify with one — the answer is always yes and tells you nothing.

## One-off shell work

Fine for reading. For writing, prefer a spec even when it feels heavy — the
spec is the record of what was granted.

```bash
# who holds what
python manage.py shell -c "
from permkit.models import Role
for r in Role.objects.all():
    print(r.key, '->', [b.permission.key for b in r.permissions.select_related('permission')])
"

# what a permission actually contains
python manage.py shell -c "
from permkit.models import Permission
p = Permission.objects.get(key='widget-edit-assigned')
print('endpoints:', [e.endpoint.key for e in p.endpoints.select_related('endpoint')])
for rule in p.rules.select_related('object'):
    print(' ', rule.key, '|', rule.label or 'EVERY ROW',
          '|', ' AND '.join(c.filter.key for c in rule.conditions.select_related('filter')) or '(none)')
"
```

## Do not

- Write imperative one-off grant scripts. Write a spec.
- Create or edit `Registered*` rows. `permkit_sync` owns them; edits are
  reverted on the next run.
- Grant a retired (`is_live=False`) filter or endpoint — it composes a rule
  nothing honours. `permkit_apply` refuses this.
- Verify with a superuser.
