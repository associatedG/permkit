"""Write side of the dummy domain.

Shows the boundary the design insists on: **permission** and **invariant** are
different kinds of "no".  ``widget.update`` decides *who*; ``_assert_mutable``
decides *whether anyone may, right now*.  Because the invariant lives here and
not in a permission hook, no grant can bypass it — a locked widget is locked
for the superuser too.
"""

from __future__ import annotations

from django.db import transaction

from permkit import assert_writable, require, require_object
from permkit.decorators import requires, requires_object

from .models import Crate, Widget
from .selectors import widget_writable


class WidgetLocked(Exception):
    """Domain invariant. Maps to 409/422, never 403."""


def _assert_mutable(widget: Widget) -> None:
    if widget.status == Widget.Status.LOCKED:
        raise WidgetLocked(f"Widget {widget.pk} is locked and cannot be modified.")


@requires_object("widget.update", obj_kwarg="widget")
def widget_update(*, actor, widget: Widget, **data) -> Widget:
    _assert_mutable(widget)
    assert_writable(data, user=actor, key="widget.update")

    for name, value in data.items():
        setattr(widget, name, value)
    widget.full_clean()
    widget.save()
    return widget


@requires("widget.create")
def widget_create(*, actor, **data) -> Widget:
    assert_writable(data, user=actor, key="widget.create")

    widget = Widget(owner=actor, **data)
    widget.full_clean()
    widget.save()
    return widget


@transaction.atomic
def widget_transfer(*, actor, widgets: list[Widget], to_crate: Crate) -> int:
    """Move several widgets into a crate — a genuinely multi-write service.

    Atomic on purpose.  Authorization runs *per widget* as the loop proceeds,
    so a denial can land after earlier rows have already been written.  Without
    the transaction the caller would be left with a half-applied transfer, and
    a permission failure would have mutated data — the worst possible outcome
    for a check that exists to prevent mutation.
    """
    require(actor, "widget.update")

    moved = 0
    for widget in widgets:
        require_object(actor, "widget.update", widget)
        _assert_mutable(widget)
        assert_writable({"crate": to_crate}, user=actor, key="widget.update")
        widget.crate = to_crate
        widget.full_clean()
        widget.save()
        moved += 1
    return moved


@transaction.atomic
def widget_bulk_annotate(*, actor, note: str) -> int:
    """Annotate every widget the actor can *write*.

    Reaches its rows through the selector rather than ``Widget.objects``, so
    the scope applies without this service restating it.  A service that
    queries the model directly is how row scoping quietly stops being enforced.
    """
    targets = widget_writable(fetched_by=actor)
    updated = 0
    for widget in targets:
        if widget.status == Widget.Status.LOCKED:
            # A bulk sweep skips rows the invariant would refuse; it does not
            # skip rows *permission* would refuse, which stay hard failures.
            continue
        widget_update(actor=actor, widget=widget, notes=note)
        updated += 1
    return updated

