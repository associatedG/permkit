---
name: permkit-init
description: Wire permkit into a Django project for the first time — INSTALLED_APPS, fail-closed DRF defaults, the admin UI for composing permissions, migrations and the first catalogue sync. Use when adding permkit to a project that does not have it, when "permkit isn't set up", when the permkit admin pages are missing, or when a permkit check raises UnknownKey because nothing has been synced.
---

# Set up permkit in a Django project

Six steps. Do them in order — step 5 fails if 1–4 are wrong, which is the point.

## 0. Find out what you are working with

```bash
python -c "import permkit; print(permkit.__file__)"   # installed?
grep -rn "INSTALLED_APPS" --include=settings*.py .    # where settings live
grep -rn "DEFAULT_PERMISSION_CLASSES" --include="*.py" .
```

If permkit is not installed, install it the way the project installs things
(check for `pyproject.toml`, `requirements*.txt`, `Pipfile`, `poetry.lock`) —
do not guess at pip.

## 1. INSTALLED_APPS

```python
INSTALLED_APPS = [
    ...,
    "permkit",
]
```

One entry. permkit is a single app; the catalogue tables live inside it.

The admin UI needs the contrib stack. Most projects already have it — check
before adding, and add only what is missing:

```python
"django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
"django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
```

## 2. Fail closed at the boundary

```python
REST_FRAMEWORK = {
    # A view that declares no permission is closed, not public. This is the
    # library's whole premise — without it, adopting permkit secures only the
    # views someone remembered to decorate.
    "DEFAULT_PERMISSION_CLASSES": ["permkit.drf.DenyAll"],
    # Layer 1 does not import DRF, so its denials need translating to 403
    # instead of surfacing as a 500.
    "EXCEPTION_HANDLER": "permkit.drf.exception_handler",
}
```

**Warn the user before you set `DenyAll`.** On an existing project it will 403
every view that has not yet been given a `permission_classes`. That is correct
and it is also an outage if it ships unnoticed. Offer both, and say which you
did:

- flip it now, and fix the views in the same change (preferred)
- leave the project default, decorate views one at a time, flip it last

If the project is not DRF, skip this step entirely — Layer 1 works without it.

## 3. Settings

Only add keys that differ from the defaults. The full set, with defaults:

```python
PERMKIT = {
    # How a user becomes role strings. The default reads user.role; it also
    # understands a list or a related manager on that attribute.
    "PRINCIPAL_RESOLVER": "permkit.principals.AttributeRoleResolver",
    "PRINCIPAL_RESOLVER_KWARGS": {"attribute": "role"},

    "STORE": "permkit.store.DatabaseStore",
    "STORE_KWARGS": {},

    # A deliberate, visible switch rather than a scattered is_superuser check.
    "SUPERUSER_BYPASS": True,

    # Resolve a user's roles once per user object rather than once per check.
    # Free for the default resolver; the difference is a resolver that reads
    # the database, where it turns a query per check into one per request.
    "CACHE_ROLES": True,

    "CONTEXT_BUILDER": None,      # callable(user) -> permkit.Context
    "DECLARATION_MODULES": None,  # None = the conventional list; see below
}
```

Find out how the project's users carry roles before setting
`PRINCIPAL_RESOLVER_KWARGS` — grep the user model for `role`, `roles`, `group`,
`is_staff`. If roles come from somewhere the default cannot reach (a JWT claim,
a profile model, an external service), write a resolver:

```python
class ClaimRoleResolver:
    def roles_for(self, user) -> list[str]:
        from permkit.models import normalize_role
        return [normalize_role(r) for r in getattr(user, "jwt_roles", []) or []]
```

If a resolver reads the database, leave `CACHE_ROLES` on — permkit asks for
roles on every check, and a list view asks again per row while stripping
fields. The cache lives on the user *instance*, so a new request resolves
afresh and revocation needs no invalidation. A long-running process that
changes somebody's role and keeps acting as them calls
`permkit.clear_role_cache(user)`.

## 4. Admin URLs

```python
urlpatterns = [
    path("admin/", admin.site.urls),   # usually already there
    ...
]
```

permkit registers its own admin automatically. Nothing else to add.

## 5. Migrate and publish the catalogue

```bash
python manage.py migrate
python manage.py permkit_sync
```

`permkit_sync` scans the project's code for declarations and writes them into
the catalogue tables. **On a fresh project it will find nothing** — that is
expected, and the next step is the `permkit-declare` skill. It is not an error.

Add both to the deploy, in this order, before the app serves traffic:

```
migrate  →  permkit_sync  →  permkit_apply <spec>   (if using specs)
```

And in CI:

```bash
python manage.py permkit_sync --check   # fails if declarations drifted
python manage.py permkit_roles          # fails on a role string matching no Role
```

Every command is documented in `docs/COMMANDS.md` in the permkit repo.

## 6. Confirm it works

```bash
python manage.py shell -c "
from permkit.catalogue.models import RegisteredEndpoint, RegisteredObject
print('objects  :', list(RegisteredObject.objects.values_list('key', flat=True)))
print('endpoints:', list(RegisteredEndpoint.objects.values_list('key', flat=True)))
"
```

Then open `/admin/` and check the **PERMKIT** section is present:

```
Declared endpoints            View          ← generated, read-only
Declared enforcement points   View
Declared field groups         View
Declared objects              View
Declared row filters          View
Permission rules              Add / Change  ← composed by people
Permissions                   Add / Change
Roles                         Add / Change
```

The read-only half is the catalogue and is rewritten by every sync. If someone
asks to edit those rows, the answer is to change the code and re-sync.

## Report back

Tell the user, specifically:
- whether you set `DenyAll` or left it, and what that means for existing views
- which resolver you configured and what attribute it reads
- that the catalogue is empty until declarations exist, and that
  `permkit-declare` is the next step

## Do not

- Add `permkit.catalogue` to `INSTALLED_APPS`. It is not an app.
- Hand-edit or fixture the `Registered*` tables. `permkit_sync` owns them.
- Create permissions here. That is `permkit-grant`, and it needs a catalogue
  to point at first.
