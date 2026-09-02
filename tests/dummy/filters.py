"""Row filters for the dummy domain.

One function per rule, registered under an object key and a filter key.  A
filter belongs to the **object**, not to a particular selector, so every read
or write path for that object shares it.

Docstrings are the labels shown to whoever composes an abstract role, so they
are written for that reader rather than for whoever maintains the query.
"""

from __future__ import annotations

from django.db.models import Q

from permkit import Param, object_filter, permission_object

from .models import Crate, Widget

permission_object("widget", model=Widget, label="Widgets")
permission_object("crate", model=Crate, label="Crates")


# -- widget ---------------------------------------------------------------


@object_filter("widget", "own")
def own(ctx) -> Q:
    """Widgets I own."""
    return Q(owner=ctx.user)


@object_filter("widget", "assigned")
def assigned(ctx) -> Q:
    """Widgets assigned to me."""
    return Q(assignee=ctx.user)


@object_filter("widget", "warehouse")
def warehouse(ctx) -> Q:
    """Widgets in the warehouse I belong to."""
    return Q(warehouse=ctx.user.warehouse)


@object_filter("widget", "status_in", params={"values": Param(list)})
def status_in(ctx, *, values) -> Q:
    """Widgets in one of the chosen statuses."""
    return Q(status__in=values)


@object_filter("widget", "watched_by_my_warehouse", multi_valued=True)
def watched_by_my_warehouse(ctx) -> Q:
    """Widgets watched by anyone in my warehouse."""
    return Q(watchers__warehouse=ctx.user.warehouse)


@object_filter("widget", "in_crate", params={"names": Param(list)})
def in_crate(ctx, *, names) -> Q:
    """Widgets filed in one of the chosen crates."""
    return Q(crate__name__in=names)


# -- crate ----------------------------------------------------------------


@object_filter("crate", "named", params={"names": Param(list)})
def crate_named(ctx, *, names) -> Q:
    """Crates with one of the chosen names."""
    return Q(name__in=names)


@object_filter("widget", "my_crates")
def my_crates(ctx) -> Q:
    """Widgets in one of the crates I am responsible for."""
    return Q(crate__in=ctx.user.crates.all())
