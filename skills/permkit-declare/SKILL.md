---
name: permkit-declare
description: Add permkit declarations to Django code — permission_object and object_condition filters, @object_permissions on selectors with apply_permissions, permission_fields on serializers, and @api_permission on views and services. Use when putting a model, endpoint, queryset or field under permission control, when a filter an admin composed never fires, or when permkit_sync reports filters-never-fire, unknown-field or an UnknownKey.
---

# Declare permissions in code

permkit has no central permissions file. Each declaration lives beside the
component it guards, and `permkit_sync` gathers them into the catalogue that
the admin composes from.

**A key is `object.endpoint`** — `widget.view`, `widget.update`. Read and write
are *different keys* carrying *different grants*, always. Never reuse one key
for both.

## Where each declaration goes

| File | Declaration | Tier |
|---|---|---|
| `filters.py` | `permission_object(...)`, `@object_condition` | object |
| `selectors.py` | `@object_permissions` + `apply_permissions()` | object |
| `serializers.py` | `permission_fields`, `permission_references` | field |
| `views.py` / `services.py` | `@api_permission` | endpoint |

Follow the project's existing layout. If it has no `selectors.py`, put the
selector where reads already live — do not restructure the app to match this
table.

## 1. Bind the object to its model — `filters.py`

```python
from permkit import Param, object_condition, permission_object
from django.db.models import Q
from .models import Widget

permission_object("widget", model=Widget, label="Widgets")
```

This is the one fact no other declaration carries. Without it the object has no
model and field groups cannot be checked against anything.

## 2. Row filters — `filters.py`

```python
@object_condition("widget", "warehouse")
def warehouse(ctx) -> Q:
    """Widgets in the warehouse I belong to."""
    return Q(warehouse=ctx.user.warehouse)


@object_condition("widget", "status_in", params={"values": Param(list)})
def status_in(ctx, *, values) -> Q:
    """Widgets in one of the chosen statuses."""
    return Q(status__in=values)


@object_condition("widget", "watched_by_my_team", multi_valued=True)
def watched_by_my_team(ctx) -> Q:
    """Widgets watched by anyone on my team."""
    return Q(watchers__team=ctx.user.team)
```

Rules for writing one:

- **It must return a `Q`.** No Python-side filtering, ever. In-memory filtering
  silently corrupts pagination, `count()` and aggregates — wrong answers, not
  just slow ones. A rule that cannot be a `Q` is a signal to denormalise, use a
  subquery, or move it to the endpoint tier.
- **The first docstring line is the admin's label.** Write it for whoever
  composes the rule, not for whoever maintains the query. "Widgets in the
  warehouse I belong to", not "filters on warehouse FK".
- **`ctx` carries request-time data** (`ctx.user`, `ctx.extra`). **`params`
  carry config-time data** an admin types. Never put user data in params.
- **`multi_valued=True` when traversing a to-many relation**, so the resolver
  adds `.distinct()`. Forgetting it yields duplicate rows.
- A filter belongs to its **object**, not to a selector. Every read path for
  that object shares it.

## 3. Scope the queryset — `selectors.py`

```python
from permkit import apply_permissions, object_permissions

@object_permissions("widget", "view")
def widget_list(*, fetched_by) -> QuerySet[Widget]:
    """Every widget this actor may read."""
    return apply_permissions(Widget.objects.all(), actor=fetched_by)


@object_permissions("widget", "update")
def widget_writable(*, fetched_by) -> QuerySet[Widget]:
    """The rows this actor may mutate — narrower than what they can read."""
    return apply_permissions(Widget.objects.all(), actor=fetched_by)
```

- The decorated function **must** take the actor as a keyword argument named
  `fetched_by` (override with `actor_kwarg=`).
- The decorator only *registers*. You choose where the narrowing lands by
  calling `apply_permissions()` — necessary when a real query needs scoping
  before an aggregate, inside a subquery, or between joins.
- **The wrapper raises if the body returns without applying**, so declaring and
  enforcing cannot drift apart.
- Give detail fetches the same scope, so a row invisible in the list cannot be
  fetched by id:

```python
def widget_get(*, fetched_by, pk) -> Widget:
    """Raises DoesNotExist for out-of-scope rows — not-found, not forbidden.

    A 403 on a read confirms the row exists, which is an existence leak.
    """
    return widget_list(fetched_by=fetched_by).get(pk=pk)
```

**Every object with filters needs at least one scope point**, or those filters
can never fire and `permkit_sync` fails with `filters-never-fire`.

## 4. Fields — `serializers.py`

```python
from permkit.drf import FieldPermissionMixin

class WidgetSerializer(FieldPermissionMixin, serializers.ModelSerializer):
    permission_object = "widget"
    permission_fields = {"money": ["secret_price"]}      # named groups
    permission_references = {"crate": "crate.view"}      # governed FKs

    class Meta:
        model = Widget
        fields = ["id", "name", "secret_price", "crate", ...]


class WidgetCreateSerializer(WidgetSerializer):
    write_endpoint = "create"   # widget.create, a separate key from update
```

- **Groups, not bare columns** — an admin grants *"Money"*, not `secret_price`,
  and adding a column to the group updates every permission that granted it.
- **Only declared fields are controlled.** Everything else passes through, so
  adopting the field tier costs one declaration per *sensitive* field, not per
  column. Do not enumerate the whole model.
- **Group names must resolve on the model** (a field or an attribute), or sync
  fails with `unknown-field`.
- `permission_references` guards *which rows an FK may point at* — writing
  `crate` is checked against `crate.view` scope. The field must actually be
  serialized, or the check never fires.
- Defaults are `read_endpoint = "view"`, `write_endpoint = "update"`.

## 5. Endpoints — `views.py` and `services.py`

```python
from permkit import api_permission
from permkit.drf import PermissionRequired

@api_permission("widget.view", label="List widgets")
class WidgetListApi(generics.ListAPIView):
    permission_classes = [PermissionRequired]
    serializer_class = WidgetSerializer

    def get_queryset(self):
        return widget_list(fetched_by=self.request.user)
```

- The `label` is what an admin sees. The first component to declare an endpoint
  supplies it; later ones just name it and are recorded as further places that
  enforce it. **Relabelling from a second component is an error** — two
  components disagreeing about what an endpoint *is* is a config bug.
- Several views may share one endpoint (a list and its detail are usually one
  permission).
- `@api_permission` sets `permission_key` for you — **unless the component
  enforces more than one endpoint**, in which case there is no single key and
  you must say which key belongs to which operation:

```python
@api_permission("product.view", label="Xem danh sách sản phẩm")
@api_permission("product.create", label="Tạo mới sản phẩm")
class ProductListCreateAPIView(generics.ListCreateAPIView):
    # Keyed by HTTP method for a generic view, or by DRF `action` for a
    # ViewSet. Without this the endpoint check denies rather than guessing.
    permission_keys = {"GET": "product.view", "POST": "product.create"}
```

Non-HTTP paths use the decorators, which work in tasks, commands and admin
actions where no DRF permission class ever runs:

```python
from permkit.decorators import requires, requires_object

@requires("widget.create")
def widget_create(*, actor, **data) -> Widget:
    assert_writable(data, user=actor, key="widget.create")
    ...

@requires_object("widget.update", obj_kwarg="widget")
def widget_update(*, actor, widget, **data) -> Widget:
    _assert_mutable(widget)          # invariant, not permission — see below
    assert_writable(data, user=actor, key="widget.update")
    ...
```

## Invariants are not permissions

"Locked rows are frozen" is a **domain rule**, not a grant. It belongs in the
service and maps to 409, so no grant — superuser included — can bypass it.

```python
def _assert_mutable(widget):
    if widget.status == Widget.Status.LOCKED:
        raise WidgetLocked(...)      # 409, never 403
```

Never express a state rule as a permission filter.

## Then, always

```bash
python manage.py permkit_sync
```

Nothing you declared exists for the admin until this runs. Read its output —
it fails the run on:

| Code | Means |
|---|---|
| `filters-never-fire` | An object has filters but no selector applies them |
| `unknown-field` | A field group names something the model lacks |
| `stale-endpoint` / `stale-filter` / `stale-group` | A composed permission points at a declaration you just removed |
| `misfiled-filter` | A rule applies one object's filter to another's key |

A `stale-*` failure after deleting a declaration is the system working. Fix the
composed permission in the admin, or put the declaration back.

## Verify with a real query, not a green test

```bash
python manage.py shell -c "
from permkit import explain
from myapp.models import User, Widget
u = User.objects.get(username='someone')
print(explain(u, 'widget.update', Widget.objects.first()))
"
```

## Common mistakes

- Reusing one key for read and write. They are separate keys, deliberately.
- Filtering in Python instead of returning a `Q`.
- Declaring filters on an object nothing scopes (`filters-never-fire`).
- A service reaching `Model.objects` directly instead of through the selector —
  the scope silently stops applying and nothing notices.
- Forgetting `multi_valued=True` on a to-many traversal, producing duplicates.
- Putting request-time data in `params`.
