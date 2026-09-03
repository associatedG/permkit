# permkit roadmap

Four tiers, built bottom-up. Each row says who writes it and where it lives.

| Tier | Lives as | Written by | State |
|---|---|---|---|
| 0. Declaration | code, beside the component | developer | **done** |
| 1. Catalogue | DB, synced from code | generated | **done** |
| 2. Composition | DB, via Django admin | admin | **done** |
| 3. Assignment | DB, via Django admin | end user | **done** |

Tier 1 is the blocker for everything above it: an admin UI cannot enumerate a
Python registry, so the declarations have to become data before anything can be
composed from them.

---

## Phase 1 — declaration ✅

Declarations live next to the components they guard. There is no central
permissions file.

```python
permission_object("widget", model=Widget)        # filters.py
@object_filter("widget", "warehouse")            # filters.py
@object_permissions("widget", "view")            # selectors.py + apply_permissions()
permission_fields = {"money": [...]}             # on the serializer
@api_permission("widget.view", label="…")        # views.py / services.py
```

Keys are **assembled** from these, not declared: `registry.key("widget.update")`
gathers the object's model, the serializer's field groups and governed
references, the scope point, and the endpoint that enforces it.

### What changed from the original plan

- `@scoped` (decorator applies the scope) became `@object_permissions` +
  an explicit `apply_permissions()` call. A real query needs the narrowing at a
  chosen point — before an aggregate, inside a subquery — which a decorator
  wrapping the return value cannot do. The guarantee survives: the decorator
  raises if the body returns without applying.
- `permission_object` was not in the plan. It carries the one fact no component
  declaration held: which model an object key stands for.
- `@api_permission` absorbed `permission_key`, so an endpoint and its enforcement are
  one statement rather than two that can disagree.
- Field groups moved onto the serializer class rather than a module-level call.
- `perms.py` is gone entirely, not "largely".

### Properties that are structural, not matters of vigilance

- a site cannot return without applying its filters
- a filter declared for one object cannot be composed onto another
- an endpoint cannot exist without a component enforcing it
- zero grants resolve to `DENY`, never an empty `Q` (which matches every row)
- object rules compile to a `Q` and reach the SQL `WHERE` clause
- domain invariants live in services, so no grant — superuser included — bypasses
  a state rule

135 tests, 3 skipped. Four of the above are mutation-verified.

---

## Phase 2 — catalogue and sync ✅

### Tables (`permkit/catalogue/models.py`)

```
RegisteredObject      key, label, model_label
RegisteredFilter      object, key, label, open_params, multi_valued
RegisteredEndpoint      key, label, targets
RegisteredFieldGroup  object, key, label, fields
RegisteredScopePoint  object, endpoint_key, target
```

Every row carries `last_seen_at` and `is_live`.

No `mode` on `RegisteredEndpoint`: the key metadata it would have projected was
dropped from the registry in phase 1, so the column would have had nothing to
populate it.

### `permkit_sync`

1. **Force-load every declaration module** before scraping — walk the URLconf
   to its leaves, and import the conventional module names in every installed
   app. The URLconf alone is not enough: a service reached only from a Celery
   task is invisible to it, and its endpoints would be published dead.
2. **Upsert, never delete.** A row missing from this run gets `is_live=False`,
   so a composition referencing it keeps working while surfacing as broken.
   A row that comes back is counted as *revived* rather than new — a nonzero
   revived count on a normal deploy means the previous run scraped a thin
   registry.
3. **Validate**, failing the run on `filters-never-fire`, `unknown-field`,
   `stale-key`, `stale-filter` and `misfiled-filter`. Validation runs *after*
   the write: the dead row it just marked is what lets someone find the
   configuration that broke, so rolling back on failure would delete the
   evidence. `--check` runs the whole thing and rolls it back, for CI.

Two things surface as warnings rather than failures, because neither breaks
enforcement: an object key mentioned by a selector or serializer but never
bound to a model (`unbound-object`), and a filter registered under no object
(`unattached-filter`). `--strict` promotes them.

### `permkit_coverage` ⬅ still outstanding

Reports models read outside a registered scope point. Advisory, with CI failing
against a committed baseline. The known example to keep honest:
`WidgetUpdateApi` reads `Widget.objects` and relies on `ScopedQuerysetMixin` —
nothing in the declaration layer would notice if a future edit dropped it.

### Tests

`tests/test_catalogue_sync.py`, 21 of them: every declaration reaches the
tables and carries what an admin needs to compose it; a forced load finds the
scope points and endpoints; a filter whose object has no scope point fails sync;
a composition referencing a removed filter is flagged rather than silently
dropped; a removed declaration is retired and a returning one revived; and
re-running sync creates, updates, revives and retires nothing.

---

## Phase 3 — composition ✅

Today's grant tables become FK-backed and gain a grouping entity — the
**abstract role**:

```
Role                     key, label, description                 # tier 3
Permission               key, name, description
PermissionEndpoint         permission, endpoint→RegisteredEndpoint
PermissionRule           permission, object, endpoint_key, label, order
PermissionRuleCondition  rule, filter→RegisteredFilter, params   # AND-ed
PermissionFieldGrant     permission, field_group, endpoint_key, allowed_values
RolePermission           role→Role, permission                   # tier 3
```

Two departures from the sketch, both forced by things the suite already
proves:

- **`PermissionFieldGrant` carries `endpoint_key`, not a READ/WRITE mode.** The
  keys are finer than that pair: `widget.update` and `widget.create` are both
  writes and are deliberately separate grants — being allowed to edit a price
  on an existing row is not being allowed to set one on a new row. A mode
  column would have silently merged them, breaking a documented property.
- **`Role` is a table.** It was not in the plan, and without it tier 3 has no
  dropdown to offer: roles were free text read off the user, nothing
  enumerated them, and a typo in an assignment granted nothing to nobody with
  no error anywhere. The `PrincipalResolver` seam is untouched — this is the
  list of role ids the system knows about, not how a user acquires one.

The free-text tables (`ObjectGrant`, `FieldGrant`, `Role*Grant`) are gone
rather than kept alongside. Nothing real was wired to them, and leaving two
grant systems in place would have meant an admin UI showing two competing
answers to "what may this role do?".

Semantics are unchanged and already proven: rules union, conditions within a
rule intersect, field groups union. `DatabaseStore` resolves through these
instead of the current free-text tables; `MemoryStore` and the resolver are
untouched, which is why the existing suite survives.

The parity test held: `test_database_store_matches_memory_store` seeds the
keeper's two hand-written grants as a composed permission and asserts the rows
are identical. The resolver and `MemoryStore` were not touched, which is why
the rest of the suite survived unchanged.

---

## Phase 4 — Django admin ✅

Django admin has no native nested inlines, so `PermissionRule` gets its own page
rather than adding a dependency.

1. **Catalogue** — read-only; `is_live` and a "used by N permissions" count.
2. **Permission** — inline endpoints and field grants; rules as a linked summary.
3. **Rule** — condition inline whose filter dropdown is **limited to live
   filters for that object**, with params rendered from `open_params`. Reads as:
   *grant access when **any** rule matches; a rule matches when **all** its
   conditions hold.*
4. **Role assignment** — role → permission checkboxes. Deliberately dull.
5. **Preview** — pick a user, a key, an object; render `policy.explain()`.
   Cheap, and it is what gives an admin confidence a rule does what they meant.
   It hangs off `PermissionAdmin.get_urls` rather than a custom `AdminSite`:
   permkit is a pluggable app, and making consumers swap their admin site for
   one extra page is a bad trade.

Building it found a real bug: `explain()` set its verdict from the endpoint
tier alone, so asking "may this keeper edit *this row*?" answered **allowed**
about a row the same policy refuses. The preview is the screen that asks that
question, which is how a latent wrong answer became a visible one.

What the screens deliberately do *not* do is render a bespoke widget per
filter param. `params` is a JSON field with the declared schema printed beside
it and validated on save — a typo is a field error, not a rule that silently
never matches. A generated widget per `Param` type is the obvious next
improvement and was not worth blocking the tier on.

---

## Phase 4.5 — permissions as a file ✅

`permkit_apply` reads a spec module (`PERMISSIONS` and `ROLES` dicts) and makes
the database match it. Deploy order is `migrate` → `permkit_sync` →
`permkit_apply`.

The split that makes it safe to run unattended: a permission the spec names is
fully managed, so removing a condition from the file removes it from the
database; role bindings are only ever added, so a deploy cannot revoke what an
administrator granted by hand. Nothing outside the spec is touched.

It validates against the catalogue before writing anything, atomically — a
missing filter, a retired endpoint, a filter belonging to another object, or
params that do not match the declaration are all refused with the whole
transaction rolled back.

## Phase 5 — hardening

- **Caching with invalidation.** Half done. Role resolution is now cached on
  the user *instance* (`CACHE_ROLES`, on by default), which needs no
  invalidation scheme because nothing outlives the object it hangs on — a
  request builds one, the next starts clean. Measured on a DB-backed resolver:
  four role lookups for one list render became one. The **grant** side is
  cached per *unit of work* — `grant_cache()`, applied to requests by
  `GrantCacheMiddleware`. A ContextVar holds a dict that the scope swaps in and
  out; there is no request id, and the same mechanism covers a Celery task.
  Measured: a 100-row page went from 105 queries to 6, flat.

  Nothing is cached outside a scope, deliberately — a ContextVar default would
  be a process-global dict nothing ever clears, and a stale grant is a security
  bug rather than a slow page. So a forgotten middleware costs queries and can
  never cost correctness.

  What is still open is caching *across* requests, which is the genuinely hard
  half: grants change in the admin while the app runs, several workers hold
  their own copies, and any window where one has not been told is a window
  where somebody keeps access they no longer have.
- **Audit trail.** Who changed which grant, when. Once non-developers can edit
  access this is a real gap. Most projects adopting permkit already have an
  audit app to hook into.
- **Registry reset between tests.** The registry is process-wide and never
  reset, so a test that registers anything leaks into every later test.
  Partly mitigated: the catalogue tests build isolated `Registry` instances
  rather than touching the global one.
- **Write-side coverage.** The scope-point check is sound for READ endpoints only;
  most write enforcement is `require_object` per row, which the registry cannot
  see.

---

## Deliberate non-goals

- **Role inheritance.** Expressed by composition instead: a manager holds the
  same abstract role plus an unscoped rule, and grants union.
- **Explicit denies.** Deny-by-absence plus AND-conditions covers every case in
  the requirements; denies would force precedence rules.
- **Separation of duties.** Would need denies. Revisit only on a real case.
- **Filtering in Python.** An object rule that cannot compile to a `Q` is a
  signal to denormalise, use a subquery, or move to the endpoint tier.
