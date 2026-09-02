# permkit

Role-based authorization for Django across three tiers, configured in data.

```
endpoint   may this role attempt this action at all
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

REST_FRAMEWORK = {
    # A view that declares no permission is closed, not public.
    "DEFAULT_PERMISSION_CLASSES": ["permkit.drf.DenyAll"],
    # Layer 1 does not import DRF, so its denials need translating to 403.
    "EXCEPTION_HANDLER": "permkit.drf.exception_handler",
}
```

## Configuration model

Registered in **code** (typed, reviewed, CI-checkable):

- **keys** — one per `(resource, action)`, carrying the model and the list
  of permission-*controlled* fields
- **object conditions** — parameterised row rules whose only method is `as_q()`

Stored as **data** (admin-editable):

- `ObjectGrant` — a key plus conditions, **AND**-ed together
- `FieldGrant` — a key plus an **allow-list** of fields
- `RoleEndpointGrant` / `RoleObjectGrant` / `RoleFieldGrant` — assignment

Grants **union**; conditions within one grant **intersect**. So more grants can
only ever add access and more conditions can only ever remove it — which is
what makes the system answerable when someone asks "why can this user do X?".
`policy.explain(user, key, obj)` prints that answer.

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

## Status

Tier 0 (declaration) and tier 1 (the catalogue and `permkit_sync`) are built
and tested. Tier 2 (composition through the catalogue) is next.

No real domain is wired to it yet. See [docs/ROADMAP.md](docs/ROADMAP.md) for
the four tiers, what each phase delivers, and the deliberate non-goals.
