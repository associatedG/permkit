# permkit

Role-based authorization for Django across three tiers, configured in data.

```
endpoint   may this role attempt this at all
object     which rows — compiled into the SQL WHERE clause
field      which fields — read and write configured separately
```

Read and write never share a grant. They are different **keys**
(`widget.view` vs `widget.update`), so "rows I may see" and "rows I may edit"
are configured independently and can differ.

## Two layers

**Layer 1 — five functions.** No request, no view, no serializer. They work in
selectors, services, Celery tasks and management commands, not only in code
paths that inherit from a DRF generic view.

```python
require(user, key)                 # endpoint  → raises PermissionDenied
apply_scope(qs, user=…, key=…)     # object read  → narrowed queryset
require_object(user, key, obj)     # object write → raises
strip_fields(data, user=…, key=…)  # field read   → pruned dict
assert_writable(data, user=…, key=…)  # field write → raises
```

**Layer 2 — ergonomics** over those: `PermissionRequired`, `ScopedQuerysetMixin`,
`FieldPermissionMixin` (`permkit.drf`) and `@requires` / `@requires_object`
(`permkit.decorators`).

## Setup

```python
INSTALLED_APPS += ["permkit"]

MIDDLEWARE += [
    # Resolves each permission question once per request instead of once per
    # row. Safe to omit — nothing is cached outside a scope.
    "permkit.cache.GrantCacheMiddleware",
]

REST_FRAMEWORK = {
    # A view that declares no permission is closed, not public.
    "DEFAULT_PERMISSION_CLASSES": ["permkit.drf.DenyAll"],
    # Layer 1 does not import DRF, so its denials need translating to 403.
    "EXCEPTION_HANDLER": "permkit.drf.exception_handler",
}
```

## Configuration model

Registered in **code** (typed, reviewed, CI-checkable), and published to the
catalogue tables by `permkit_sync`:

- **objects** — what filters and field groups are about, bound to a model
- **filters** — parameterised row rules whose only method is `as_q()`
- **endpoints** — the operations a component enforces, one per key
- **field groups** — named bundles of fields, declared on the serializer

Composed as **data**, in the Django admin, entirely by foreign key into that
catalogue:

```
Permission                a grantable bundle — the abstract role
  PermissionEndpoint        endpoints it may reach
  PermissionRule          rows it may act on — several, OR-ed
    PermissionRuleCondition   narrowing within one rule — AND-ed
  PermissionFieldGrant    fields it may see or write
RolePermission            which roles hold it
```

Rules **union**; conditions within one rule **intersect**; field groups union.
So more rules can only ever add access and more conditions can only ever
remove it — which is what makes the system answerable when someone asks "why
can this user do X?". The admin's preview screen prints that answer.

A permission is a *job*, not a person: "browse every widget" is one permission
that the admin and the viewer both hold, and the admin is distinguished by
holding several more. That is also how role inheritance is avoided — a manager
holds the keeper's permission plus an unscoped one, and grants union.

## Design rules the tests enforce

| Rule | Why |
|---|---|
| Object rules must compile to a `Q` | In-memory filtering silently corrupts pagination, `count()` and aggregates — wrong answers, not just slow ones |
| List and detail resolve from one `Q` | Otherwise a row can be missing from the list yet fetchable by id |
| Zero grants → `DENY`, never `Q()` | An empty `Q` matches *everything*; a user with no permissions would get the whole table |
| An unconditional grant short-circuits to `ALL` | Django collapses `Q() \| Q(x=1)` to `Q(x=1)`, which would *narrow* instead of widen |
| Fields are allow-lists | Hide-lists break monotonicity: unioning two grants would shrink what you see |
| Only declared fields are controlled | Adoption costs one declaration per *sensitive* field, not per column |
| Invariants are not permissions | "Locked rows are frozen" belongs in the service (→409), so no grant — not even superuser — can bypass it |
| Unregistered key raises in the core, denies at the boundary | A typo must not be indistinguishable from a deliberate deny |

## Running the tests

```bash
pip install -e '.[dev]'
PYTHONPATH=src:. python -m pytest -q
```

The suite runs entirely against the synthetic `Widget` domain in `tests/dummy`.
If a rule cannot be expressed against `Widget`, that is evidence the
abstraction is wrong — not a reason to import a production model.

## The catalogue

The registry lives in memory, which is enough to enforce with and useless to
compose from: an admin UI cannot enumerate a Python object graph, and a grant
cannot hold a foreign key into one. `permkit_sync` publishes it as rows.

```bash
python manage.py migrate
python manage.py permkit_sync          # after every deploy
python manage.py permkit_sync --check  # in CI: writes nothing, fails on drift
```

It forces every declaration module in before scraping — a sync run never
serves a request, so it cannot rely on the URLconf having imported the code
the way a first request would. It then upserts, **never deletes**: a
declaration that has gone from the code is marked `is_live=False`, so a grant
pointing at it keeps resolving exactly as it did while showing up as broken.

Finally it fails the run on the ways code and configuration drift apart:

| Code | Means |
|---|---|
| `filters-never-fire` | An object has filters but no selector applies them — nothing an admin composes from them can take effect |
| `unknown-field` | A field group names a column the model does not have |
| `stale-key` / `stale-filter` | A grant references a key or filter no declaration mentions any more |
| `misfiled-filter` | A grant applies one object's filter to another's key — it would filter on the wrong column |

## Permissions as a file

The admin is where permissions are composed. For environments with nobody
sitting in front of them — a fresh database, CI, a deploy — a spec file is the
same composition in a form you can review and re-run:

```python
PERMISSIONS = {
    "widget-edit-assigned": {
        "name": "Edit widgets assigned to me",
        "endpoints": ["widget.update"],
        "rules": [{
            "key": "widget.update",
            "label": "in my warehouse and assigned to me",
            "conditions": [{"filter": "widget.warehouse"},
                           {"filter": "widget.assigned"}],
        }],
    },
}
ROLES = {"w_keeper": {"label": "Warehouse keeper",
                      "permissions": ["widget-edit-assigned"]}}
```

```bash
python manage.py permkit_apply myapp/permissions/baseline.py
python manage.py permkit_apply myapp/permissions/baseline.py --check   # CI
```

A permission the spec names is **fully managed** — delete a condition from the
file and it goes from the database, or the file is a lie. Role bindings are
**only ever added**, because a deploy must not revoke what somebody granted in
the admin an hour ago. Permissions the spec does not name are untouched.

`tests/dummy/permissions.py` is the worked example.

## Seeing it

The dummy domain is runnable, so the admin can be opened against real rules:

```bash
export PERMKIT_DB=demo.sqlite3
python manage.py migrate
python manage.py permkit_sync          # publish the declarations
python manage.py seed_dummy_roles --superuser   # apply the spec, add sample rows
python manage.py runserver
```

Then `/admin/permkit/permission/` to compose, `/admin/permkit/role/` to assign,
and `/admin/permkit/permission/preview/` to ask why a given user can or cannot
do a given thing to a given row. The catalogue pages are read-only: an edit
there would be silently reverted by the next sync.

`--superuser` creates a local `admin`/`admin` login. It is a superuser, and
permkit's superuser bypass means it passes every check regardless of what you
compose — use the preview with `demo_keeper_1` to watch real rules resolve.

## Claude Code skills

`skills/` holds three skills for working with permkit, versioned alongside the
API they describe:

| Skill | For |
|---|---|
| `permkit-init` | Wiring permkit into a project the first time |
| `permkit-declare` | Adding declarations in the code layer |
| `permkit-grant` | Composing permissions and roles, and asking "why?" |

Install by symlinking them into `~/.claude/skills/`.

## Status

Tiers 0–4 are built and tested: declaration, the catalogue, composition,
assignment and the admin. 226 tests, 3 skipped.

No real domain is wired to it yet — the whole suite runs against the synthetic
`Widget` domain in `tests/dummy`, which is also what the demo above serves.
See [docs/COMMANDS.md](docs/COMMANDS.md) for every command and what it
fails on, and [docs/ROADMAP.md](docs/ROADMAP.md) for what each phase delivered, what is
still outstanding (`permkit_coverage`, caching with invalidation, an audit
trail) and the deliberate non-goals.
