"""Read side of the dummy domain.

Each selector declares itself a permission site with ``@object_permissions``
and then chooses where the narrowing lands by calling ``apply_permissions``.
The placement matters in a real query — before an aggregate, inside a
subquery, between joins — which is why the decorator registers but does not
apply.

Registering without enforcing is still impossible: the decorator raises if the
body returns without having applied the filters.
"""

from __future__ import annotations

from django.db.models import QuerySet

from permkit import apply_permissions, object_permissions

from .models import Widget


@object_permissions("widget", "view")
def widget_list(*, fetched_by) -> QuerySet[Widget]:
    return apply_permissions(Widget.objects.all(), actor=fetched_by)


@object_permissions("widget", "update")
def widget_writable(*, fetched_by) -> QuerySet[Widget]:
    """The rows this actor may mutate — a narrower set than they can read."""
    return apply_permissions(Widget.objects.all(), actor=fetched_by)


def widget_get(*, fetched_by, pk) -> Widget:
    """Raises ``Widget.DoesNotExist`` for out-of-scope rows.

    Not-found rather than forbidden: a 403 on a read would confirm the row
    exists, which is an existence leak.  Drawn from ``widget_list`` so a row
    invisible in the list cannot be fetched by id.
    """
    return widget_list(fetched_by=fetched_by).get(pk=pk)
