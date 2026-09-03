---
name: permkit-audit
description: Review a permkit setup end to end for gaps and dead configuration — roles that can reach an endpoint but see no rows, rules that never fire, endpoints nobody grants, filters nobody composed, permissions nobody holds, and role strings matching no Role. Use when asked to check, audit, review or sanity-check permissions, when someone "can't see anything" or "sees everything", or before shipping a permission change.
---

# Audit a permkit setup

Two kinds of problem, and they need different eyes:

- **Gaps** — someone is silently denied, or a rule silently does nothing. These
  are bugs. Report them first.
- **Dead configuration** — declared or composed and never used. Usually fine,
  sometimes the sign of a half-finished change. Report second, don't alarm.

Run every step. Report findings ranked by what actually breaks for a person.

## 1. The two commands first

```bash
python manage.py permkit_sync --check
python manage.py permkit_roles
```

The first fails if the catalogue is out of step with the code, or if a
composed permission points at something the code no longer declares. The second
fails if a user's role string matches no `Role` row — those people are denied
everything and look exactly like people deliberately given nothing.

If either fails, fix that before going further: everything below is read
against the catalogue, and a stale catalogue makes the rest misleading.

## 2. The gap checks

Save as a file and run with `python manage.py shell < audit.py` — these are
too long for `-c`.

```python
from permkit.catalogue.models import (
    RegisteredEndpoint, RegisteredFieldGroup, RegisteredFilter,
)
from permkit.models import Permission, Role
from permkit.registry import registry

# Only keys with a scope point need rules; widget.create has no row to scope.
scoped = {f"{o}.{e}" for o, e in registry.scope_points}

def perms_of(role):
    return [b.permission for b in role.permissions.all()]

def reach_of(perms):
    return {e.endpoint.key for p in perms for e in p.endpoints.select_related("endpoint")}

def rules_of(perms):
    return {r.key for p in perms for r in p.rules.select_related("object")}

roles = Role.objects.prefetch_related("permissions__permission")

print("A. reaches an endpoint but has no rows for it")
for role in roles:
    perms = perms_of(role)
    ruled = rules_of(perms)
    for key in sorted(reach_of(perms) & scoped):
        if key not in ruled:
            print(f"   {role.key} may reach {key} but has no rule -> sees zero rows")

print("B. has rules for a key it cannot reach")
for role in roles:
    perms = perms_of(role)
    reach = reach_of(perms)
    for key in sorted(rules_of(perms)):
        if key not in reach:
            print(f"   {role.key} has rules for {key} but no endpoint grant -> never fires")

print("C. declared endpoint nobody grants")
for e in RegisteredEndpoint.objects.filter(is_live=True, granted_by__isnull=True):
    print(f"   {e.key} — enforced in code, no permission grants it")

print("D. declared filter no rule uses")
for f in RegisteredFilter.objects.filter(is_live=True, used_by__isnull=True):
    print(f"   {f.key} — available to compose, never composed")

print("E. declared field group nobody grants")
for g in RegisteredFieldGroup.objects.filter(is_live=True, granted_by__isnull=True):
    print(f"   {g.object.key}.{g.key} ({', '.join(g.fields)}) — hidden from everyone")

print("F. permission nobody holds")
for p in Permission.objects.filter(role_bindings__isnull=True):
    print(f"   {p.key} — composed, assigned to no role")

print("G. retired but still referenced")
for f in RegisteredFilter.objects.filter(is_live=False, used_by__isnull=False).distinct():
    print(f"   filter {f.key} is retired but a rule still uses it")
```

## 3. What each finding means

Severity is about the person on the other end, not the tidiness of the data.

| | Finding | Severity | Why |
|---|---|---|---|
| **A** | Reaches an endpoint, no rules | **Bug** | `scope()` returns DENY with no object grants. They pass the endpoint check and then see an empty list — which reads as "no data", not "no permission". The most confusing failure in the system. |
| **B** | Rules but no endpoint grant | **Bug** | `scope()` checks the endpoint grant *first* and returns DENY without ever evaluating the rules. Somebody composed careful conditions that cannot fire. |
| **G** | Retired but referenced | **Bug** | The declaration left the code. The rule still resolves as it did, but nobody can reason about or edit it. `permkit_sync` also fails on this. |
| **C** | Endpoint nobody grants | **Check** | A route exists that no role can use. Either a feature nobody was given, or a permission somebody forgot to compose. |
| **E** | Field group nobody grants | **Check** | That field is stripped from every response for everyone. Often deliberate — sometimes the grant was forgotten. |
| **D** | Filter nobody composed | **Info** | Normal. A codebase declares more filters than any role happens to use. Only worth mentioning if the user just added it. |
| **F** | Permission nobody holds | **Info** | Dead config, or a permission staged before its role assignment. |

**A is the one to lead with.** "I can log in but the list is empty" is the
complaint it produces, and nothing in the logs says why.

## 4. Confirm a finding before reporting it

Never report a gap from a query alone — prove it against a real user:

```bash
python manage.py shell -c "
from permkit import explain
from myapp.models import User
print(explain(User.objects.filter(role='w_keeper').first(), 'widget.view'))
"
```

The trace names the roles, whether the endpoint grant was found, every rule
with its conditions, and which fields are withheld. If it disagrees with your
query, trust the trace — it is the code that actually decides.

And count rows per role, which catches what queries miss:

```bash
python manage.py shell -c "
from myapp.selectors import widget_list
from myapp.models import User
for u in User.objects.all()[:10]:
    print(f'{u.username:20} {u.role:14} sees {widget_list(fetched_by=u).count()}')
"
```

A role seeing **0** where it should see some is finding A. A role seeing
**everything** where it should be narrowed usually means an unconditional rule
— a rule with no conditions means every row, deliberately.

**Never audit with a superuser.** `SUPERUSER_BYPASS` makes every check pass, so
the answer is always yes and tells you nothing.

## 5. Report

Lead with what breaks for a person, then what is merely untidy:

```
2 gaps

  w_viewer can reach widget.view but has no rule for it.
  Anyone with that role gets an empty list, with no error. They need a
  permission holding a widget.view rule — "every row" if that is the intent.

  w_support has rules for widget.update but no endpoint grant, so the rules
  never run. Add widget.update to a permission that role holds, or drop the
  rules.

3 unused, probably fine

  widget.status_in, widget.own, widget.my_crates — declared, never composed.
  Normal unless one was just added for a rule somebody meant to write.
```

Say what to do, not just what is wrong. Do not report clean checks — "no
findings for C through G" is noise.

## Do not

- Report a gap without confirming it with `explain()` or a row count.
- Treat D or F as problems by default. Most codebases have both.
- Audit as a superuser.
- Fix anything without being asked. This skill reports; `permkit-grant`
  changes things.
