# The dummy service

A small warehouse domain, wired to permkit end to end. Every read and write
path is routed, so a permission decision can be traced from an HTTP request
down to the grant that allowed or refused it.

Nothing here is a permkit test. This is the *consumer*: an ordinary Django
app that happens to be fully instrumented. Its own tests live in `tests/`.

## The domain

`Widget` — the thing being protected. Belongs to a `warehouse` (a string,
matched against the user's own), has an `owner` and an optional `assignee`,
an optional `crate`, a `status`, and a `secret_price`.

`Crate` — a container a widget can be filed into. Exists mainly so that a
write can carry a **reference to another object**.

`User` — carries `role` (one string) and `warehouse`.

## What is declared, and where

| File | Declares | Tier |
|---|---|---|
| `filters.py` | `permission_object` for widget and crate; every `@object_condition` | object |
| `selectors.py` | `@object_permissions` scope sites | object |
| `serializers.py` | `permission_fields`, `permission_references` | field |
| `views.py` | `@api_permission` — one per endpoint, on the route that is it | endpoint |
| `services.py` | nothing; it *enforces* with `@requires`, it does not declare | — |

Declaring and enforcing are separate. A view declares that an endpoint
exists, and gives it a label, because a view is the endpoint. A service
enforces it with `@requires` / `@requires_object`, and a management
command enforces the same key with no view in sight.

None of this grants anything. It registers the vocabulary an administrator
composes grants from. The grants themselves are rows — see `conftest.grants`
for the set these tests use.

## Keys

| Key | Meaning |
|---|---|
| `widget.view` | which widgets may be read |
| `widget.update` | which widgets may be mutated |
| `widget.create` | may create widgets at all |
| `crate.view` | which crates may be seen *and filed into* |

`widget.view` and `widget.update` are separate keys on purpose: "rows I may
see" and "rows I may edit" are configured independently and routinely differ.

`crate.view` is never declared by an `@api_permission` in `services.py`. It comes
into existence because `serializers.py` names it as a governed reference, and
`filters.py` binds `crate` to the `Crate` model.

## Read paths

### R1 · `GET /widgets/` → `WidgetListApi` → `widget_list`

| Tier | Check | Failure |
|---|---|---|
| endpoint | `widget.view` grant | 403 |
| object | `Q` from the object grants | rows silently absent |
| field | `strip_fields` on render | field silently absent |

No grant at all is a 403. A grant with no matching rows is `200 []`. The
difference matters: the empty state never has to hedge.

### R2 · `GET /widgets/<pk>/` → `WidgetDetailApi` → `widget_list`

Same key, same `Q`. Because the detail draws from the list's queryset, an
out-of-scope row is **404, not 403** — a 403 would confirm the row exists,
which is an existence leak. It also means a row cannot be missing from the
list yet fetchable by id.

### R3 · `GET /crates/` → `CrateListApi` → `crate_list`

The crate picker. The rows it returns are exactly the rows a widget may be
filed into, because W3's reference check resolves through this same scope.
Picker and validation cannot disagree.

### R4 · `GET /widgets/writable/` → `WidgetWritableListApi`

"The widgets I may edit." The one route that reads `Widget.objects` directly
and relies on `ScopedQuerysetMixin` rather than a selector — the shape most
existing Django code already has. Its `read_key` is `widget.update`, so it
returns the narrower set.

Deliberately a **read** route. A generic view that *writes* enforces the
permission tiers and silently skips the domain invariant.

## Write paths

### W1 · `POST /widgets/create/` → `WidgetCreateApi` → `widget_create`

| Tier | Check | Failure |
|---|---|---|
| endpoint | `widget.create` grant | 403 |
| object | not used — no row exists yet | — |
| field | `assert_writable` against `widget.create` | 403 |

Uses `WidgetCreateSerializer`, whose `write_endpoint = "create"`. An admin who
may edit prices on an existing row has not thereby been granted the right to
set one on a new row.

### W2 · `PATCH /widgets/<pk>/update/` → `WidgetUpdateApi` → `widget_update`

The full path. In order:

| # | Check | Failure |
|---|---|---|
| 1 | endpoint: `widget.update` grant | 403 |
| 2 | object: row in the **write** scope | 404 |
| 3 | field: may write these fields | 403 |
| 4 | value: may set these values | 403 |
| 5 | reference: `crate` within `crate.view` scope | 403 |
| 6 | invariant: row is not `LOCKED` | **409** |

Step 2 is a 404 rather than a 403 for the same reason as R2, applied to the
narrower set: a row you may read but not edit is simply not on this route.

Step 6 is not a permission. No grant bypasses it, superuser included, which
is why it lives in the service and has no key. It is routed through
`widget_update` rather than `serializer.save()` precisely so it cannot be
skipped — a generic view here would return `200` on a locked row.

### W3 · `POST /widgets/<pk>/transfer/` → `WidgetTransferApi` → `widget_transfer`

Isolates the reference check. Body is `{"crate": <id>}`.

The destination crate is fetched **unscoped** on purpose, so what refuses an
out-of-scope crate is the reference check inside `assert_writable` — not a
lucky 404 from the fetch. Being allowed to write the `crate` column and being
allowed to choose *which* crate are separate gates.

`widget_transfer` is `@transaction.atomic` because authorization runs per row
inside its loop, so a denial can land after earlier rows have been written.

### N1 · `manage.py annotate_widgets` → `widget_bulk_annotate`

No HTTP, no view, no DRF permission class. Reaches its rows through
`widget_writable`, so the same scope applies without the command restating
it. Proves the guarantee is not view-shaped.

## The two kinds of "no"

| | meaning | status | bypassable by a grant |
|---|---|---|---|
| `PermissionDenied` | you may not | 403 / 404 | yes, by granting |
| `WidgetLocked` | nobody may, right now | 409 | no |

Confusing these is the most common way an authorization layer goes wrong.
"Locked rows are frozen" is a business rule; if it were a permission, some
role would eventually be granted past it.
