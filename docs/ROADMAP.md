# permkit roadmap

Four tiers, built bottom-up. Each row says who writes it and where it lives.

| Tier | Lives as | Written by | State |
|---|---|---|---|
| 0. Declaration | code, beside the component | developer | **done** |
| 1. Catalogue | DB, synced from code | generated | **next** |
| 2. Composition | DB, via Django admin | admin | not started |
| 3. Assignment | DB, via Django admin | end user | not started |

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
@api_action("widget.view", label="…")            # views.py / services.py
```

Keys are **assembled** from these, not declared: `registry.key("widget.update")`
gathers the object's model, the serializer's field groups and governed
references, the scope point, and the action's mode.

### What changed from the original plan

- `@scoped` (decorator applies the scope) became `@object_permissions` +
  an explicit `apply_permissions()` call. A real query needs the narrowing at a
  chosen point — before an aggregate, inside a subquery — which a decorator
  wrapping the return value cannot do. The guarantee survives: the decorator
  raises if the body returns without applying.
- `permission_object` was not in the plan. It carries the one fact no component
  declaration held: which model an object key stands for.
- `@api_action` absorbed `permission_key`, so an action and its enforcement are
  one statement rather than two that can disagree.
- Field groups moved onto the serializer class rather than a module-level call.
- `perms.py` is gone entirely, not "largely".

### Properties that are structural, not matters of vigilance

- a site cannot return without applying its filters
- a filter declared for one object cannot be composed onto another
- an action cannot exist without a component enforcing it
- zero grants resolve to `DENY`, never an empty `Q` (which matches every row)
- object rules compile to a `Q` and reach the SQL `WHERE` clause
- domain invariants live in services, so no grant — superuser included — bypasses
  a state rule

135 tests, 3 skipped. Four of the above are mutation-verified.

---

## Phase 2 — catalogue and sync ⬅ next

### Tables (`permkit/catalogue/models.py`)

```
RegisteredObject      key, label, model_label
RegisteredFilter      object, key, label, open_params, multi_valued
RegisteredAction      key, label, mode, targets
RegisteredFieldGroup  object, key, label, fields
RegisteredScopePoint  object, action, target
```

Every row carries `last_seen_at` and `is_live`.

### `permkit_sync`

1. **Force-load every declaration module** before scraping — resolve the
   URLconf, import service modules. Verified problem: after app startup the
   dummy registry holds two actions, not three, because `services.py` is only
   imported when something asks for it. A sync run today would publish an
   incomplete catalogue and then mark a live action dead on the next run.
2. **Upsert, never delete.** A row missing from this run gets `is_live=False`,
   so a composition referencing it keeps working while surfacing as broken.
3. **Validate**, failing the build on:
   - a composition referencing a filter, action or group no longer in code
   - an object with filters but no scope point — those filters can never fire
   - a field group naming a field the model does not have

### `permkit_coverage`

Reports models read outside a registered scope point. Advisory, with CI failing
against a committed baseline. The known example to keep honest:
`WidgetUpdateApi` reads `Widget.objects` and relies on `ScopedQuerysetMixin` —
nothing in the declaration layer would notice if a future edit dropped it.

### Tests

- `@scoped` sites and actions are all present after a forced load
- a filter whose object has no scope point fails sync
- a composition referencing a removed filter is flagged, not silently dropped
- re-running sync is idempotent

---

## Phase 3 — composition

Today's grant tables become FK-backed and gain a grouping entity — the
**abstract role**:

```
Permission               key, name, description
PermissionAction         permission, action→RegisteredAction
PermissionRule           permission, object, action_key, order   # one grant
PermissionRuleCondition  rule, filter→RegisteredFilter, params   # AND-ed
PermissionFieldGrant     permission, field_group, mode           # READ|WRITE
RolePermission           role, permission                        # tier 3
```

Semantics are unchanged and already proven: rules union, conditions within a
rule intersect, field groups union. `DatabaseStore` resolves through these
instead of the current free-text tables; `MemoryStore` and the resolver are
untouched, which is why the existing suite survives.

The parity test to keep: a permission composed of two rules must produce the
same `Q` as today's hand-written grants.

---

## Phase 4 — Django admin

Django admin has no native nested inlines, so `PermissionRule` gets its own page
rather than adding a dependency.

1. **Catalogue** — read-only; `is_live` and a "used by N permissions" count.
2. **Permission** — inline actions and field grants; rules as a linked summary.
3. **Rule** — condition inline whose filter dropdown is **limited to live
   filters for that object**, with params rendered from `open_params`. Reads as:
   *grant access when **any** rule matches; a rule matches when **all** its
   conditions hold.*
4. **Role assignment** — role → permission checkboxes. Deliberately dull.
5. **Preview** — pick a role, a sample user, an object; render
   `policy.explain()`. Cheap, and it is what gives an admin confidence a rule
   does what they meant.

---

## Phase 5 — hardening

- **Caching with invalidation.** There is none today; every check hits the
  store. Revocation must take effect promptly, so this is correctness, not
  performance.
- **Audit trail.** Who changed which grant, when. Once non-developers can edit
  access this is a real gap; `pmso-service` already has an `audit` app to hook.
- **Registry reset between tests.** The registry is process-wide and never
  reset, so a test that registers anything leaks into every later test.
- **Write-side coverage.** The scope-point check is sound for READ actions only;
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
