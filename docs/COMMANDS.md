# permkit commands

Four commands. Two belong in every deploy, two are checks you run when
something looks wrong.

| Command | Reads | Writes | In a deploy? |
|---|---|---|---|
| `permkit_sync` | your code | the catalogue tables | **yes**, after `migrate` |
| `permkit_apply <spec>` | a spec file | permissions and roles | **yes**, after `permkit_sync` |
| `permkit_roles` | users + roles | nothing | no — a check |
| *(the admin)* | — | permissions and roles | — |

Deploy order, and it matters:

```bash
python manage.py migrate
python manage.py permkit_sync
python manage.py permkit_apply myapp/permissions/baseline.py
```

`permkit_apply` points at catalogue rows by foreign key, so `permkit_sync` has
to have run or there is nothing to point at.

---

## `permkit_sync`

Publishes what your code declares into the catalogue tables, so the admin has
something to compose from.

```bash
python manage.py permkit_sync
python manage.py permkit_sync --check     # CI: writes nothing, fails on drift
python manage.py permkit_sync --strict    # warnings become failures
python manage.py permkit_sync --no-load   # diagnostic; skip the forced import
python manage.py permkit_sync -v 2        # list every module it imported
```

It imports every declaration module first — walking the URLconf and importing
conventional module names in each app — because a sync run never serves a
request and cannot rely on the code having been imported the way a first
request would.

It **upserts, never deletes**. A declaration that has left the code is marked
`is_live=False` so a permission pointing at it keeps resolving while showing up
as broken.

Then it fails the run on:

| Code | Means | Fix |
|---|---|---|
| `filters-never-fire` | An object has filters but no selector applies them | Add `@object_permissions` + `apply_permissions()` |
| `unknown-field` | A field group names something the model lacks | Fix the group, or the model |
| `stale-endpoint` | A permission grants an endpoint the code no longer declares | Remove the grant, or restore the declaration |
| `stale-filter` | A rule uses a filter the code no longer declares | Same |
| `stale-group` | A permission grants a field group nothing declares | Same |
| `stale-object` / `stale-key` | A rule is for a key no declaration mentions | Same |
| `misfiled-filter` | A rule applies one object's filter to another's key | Fix the rule — it would filter on the wrong column |

Warnings (not failures unless `--strict`): `unbound-object`, a key mentioned by
a selector or serializer but never bound to a model; `unattached-filter`, a
filter registered under no object.

---

## `permkit_apply <spec>`

Makes the database match a permission spec — a module with `PERMISSIONS` and
`ROLES` dicts. See `tests/dummy/permissions.py` for a worked example.

```bash
python manage.py permkit_apply myapp/permissions/baseline.py
python manage.py permkit_apply myapp.permissions.baseline   # dotted also works
python manage.py permkit_apply <spec> --check               # CI
```

What it manages, exactly:

- **A permission the spec names is fully managed.** Endpoints, rules and field
  grants are made to match the file, so removing a condition from the file
  removes it from the database.
- **Role bindings are only ever added.** A deploy must not revoke what someone
  granted in the admin an hour ago.
- **Permissions the spec does not name are never touched.**

It validates before writing anything, atomically, and refuses a missing or
retired endpoint/filter/field group, a filter belonging to a different object,
and params that do not match the filter's declaration.

---

## `permkit_roles`

Finds role names that do not line up. A user's role reaches permkit as a
*string*, matched by name against the `Role` table — which is what keeps
permkit out of your user model, and is the one place a typo is silent.

```bash
python manage.py permkit_roles
python manage.py permkit_roles --strict
```

```
  4 role(s) defined, 5 user(s) scanned
  w_admin                    1 user(s)   6 permission(s)
  w_keeper                   1 user(s)   3 permission(s)

  'w_keper' is held by 1 user(s) but matches no Role. They are granted
  nothing, and nothing says so.
```

**Fails** on a role string held by users that matches no `Role` row — that user
is denied everything and looks identical to one deliberately given nothing.

**Warns** on: a `Role` nobody holds, a `Role` that grants nothing, and users
with no role at all (often intended). `--strict` promotes the first two.

Works with any `PrincipalResolver`. For a plain column it is one query; for
anything else it asks the resolver user by user and says so.

---

## Not a command: the admin

```
/admin/permkit/permission/           compose permissions
/admin/permkit/role/                 assign them to roles
/admin/permkit/permission/preview/   ask why a user can or cannot do something
/admin/permkit/registeredfilter/     what the code declares (read-only)
```

The `Declared *` pages are generated by `permkit_sync` and are read-only. An
edit there would be reverted by the next sync.

---

## Debugging without a command

```bash
# why can this user do this to this row?
python manage.py shell -c "
from permkit import explain
from myapp.models import User, Widget
print(explain(User.objects.get(username='x'), 'widget.update', Widget.objects.first()))
"

# what does the catalogue actually hold?
python manage.py shell -c "
from permkit.catalogue.models import *
print('endpoints:', list(RegisteredEndpoint.objects.values_list('key', flat=True)))
print('filters  :', list(RegisteredFilter.objects.values_list('key', flat=True)))
"

# what does this permission contain?
python manage.py shell -c "
from permkit.models import Permission
p = Permission.objects.get(key='widget-edit-assigned')
for rule in p.rules.select_related('object'):
    print(rule.key, '|', ' AND '.join(c.filter.key for c in rule.conditions.all()) or 'EVERY ROW')
"
```

A superuser passes every check while `SUPERUSER_BYPASS` is on, so never debug
with one — the answer is always yes and tells you nothing.

If a *grant* lookup appears once per row, no grant-cache scope is open. Add
`permkit.cache.GrantCacheMiddleware` after authentication, or wrap the work in
`with permkit.grant_cache():`. Nothing is cached outside a scope, so this is a
performance setting and never a correctness one.

If you are counting queries and a role lookup appears more than once per
request, the user object is being rebuilt between checks. Roles are cached per
user *instance* (`CACHE_ROLES`, on by default); `permkit.clear_role_cache(user)`
forces a re-read for a long-running process that changed somebody's role.
