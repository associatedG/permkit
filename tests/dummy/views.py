"""The dummy domain's HTTP surface — one view per read or write path.

Every path the domain supports is routed, so a permission decision can be
traced end to end from a request.  Each view names exactly one key, and the
tiers that key triggers are listed in ``PERMISSIONS.md``.

Two integration styles appear here, both real:

* **Views call the selector or the service.** One enforcement path for every
  caller — HTTP, task, command — so nothing is enforced at the view that is
  not also enforced underneath it.  This is the pattern to prefer, and the
  mutation routes use it so that domain invariants cannot be bypassed by
  writing through a serializer.
* **The list route uses the selector directly**, which is the shape most
  existing Django code already has.
"""

from __future__ import annotations

from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from permkit import api_permission
from permkit.drf import PermissionRequired, ScopedQuerysetMixin

from .models import Crate, Widget
from .selectors import crate_list, widget_get_writable, widget_list
from .serializers import CrateSerializer, WidgetCreateSerializer, WidgetSerializer
from .services import WidgetLocked, widget_create, widget_transfer, widget_update


def _ctx(request) -> dict:
    return {"request": request}


# -- read paths -----------------------------------------------------------


@api_permission("widget.view", label="List widgets")
class WidgetListApi(generics.ListAPIView):
    """R1 — endpoint tier, object tier, field tier on render."""

    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]

    def get_queryset(self):
        return widget_list(fetched_by=self.request.user)


@api_permission("widget.view")
class WidgetDetailApi(generics.RetrieveAPIView):
    """R2 — the same action as the list.

    A list and its detail are one permission, so both name ``widget.view``.
    Because the queryset is the selector's, an out-of-scope row is a 404: the
    list and the detail cannot disagree.
    """

    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]

    def get_queryset(self):
        return widget_list(fetched_by=self.request.user)


@api_permission("crate.view", label="List crates")
class CrateListApi(generics.ListAPIView):
    """R3 — the crate picker.

    The rows here are exactly the rows a widget may be filed into, because
    the FK reference check resolves through this same ``crate.view`` scope.
    """

    serializer_class = CrateSerializer
    permission_classes = [PermissionRequired]

    def get_queryset(self):
        return crate_list(fetched_by=self.request.user)


class WidgetWritableListApi(ScopedQuerysetMixin, generics.ListAPIView):
    """R4 — "the widgets I may edit", as a generic view.

    The one route that reads ``Widget.objects`` directly and leans on
    ``ScopedQuerysetMixin`` rather than a selector — the shape most existing
    Django code already has.  Safe here because a read has no invariant to
    skip; the mutation routes deliberately do not use this pattern.

    ``read_key`` is the *write* key, so this returns the narrower set.
    """

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]
    permission_key = "widget.update"
    read_key = "widget.update"


# -- write paths ----------------------------------------------------------


@api_permission("widget.create", label="Create a widget")
class WidgetCreateApi(APIView):
    """W1 — endpoint and field tiers only.

    There is no row yet, so there is nothing for the object tier to scope.
    """

    permission_classes = [PermissionRequired]
    permission_key = "widget.create"

    def post(self, request):
        serializer = WidgetCreateSerializer(data=request.data, context=_ctx(request))
        serializer.is_valid(raise_exception=True)

        widget = widget_create(actor=request.user, **serializer.validated_data)
        return Response(
            WidgetSerializer(widget, context=_ctx(request)).data,
            status=status.HTTP_201_CREATED,
        )


@api_permission("widget.update", label="Update a widget")
class WidgetUpdateApi(APIView):
    """W2 — every tier, plus the domain invariant.

    Routed through ``widget_update`` rather than a generic view's
    ``serializer.save()``.  A generic view would enforce the permission tiers
    and silently skip ``_assert_mutable``, letting a locked row be written
    over HTTP while the service refused the same change.
    """

    permission_classes = [PermissionRequired]
    permission_key = "widget.update"

    def patch(self, request, pk):
        try:
            widget = widget_get_writable(fetched_by=request.user, pk=pk)
        except Widget.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = WidgetSerializer(
            widget, data=request.data, partial=True, context=_ctx(request)
        )
        serializer.is_valid(raise_exception=True)

        try:
            widget = widget_update(
                actor=request.user, widget=widget, **serializer.validated_data
            )
        except WidgetLocked as exc:
            # An invariant, not a permission: 409, never 403.
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(WidgetSerializer(widget, context=_ctx(request)).data)


@api_permission("widget.update")
class WidgetTransferApi(APIView):
    """W3 — the FK reference check, on its own route.

    The destination crate is looked up *unscoped* on purpose, so that what
    refuses an out-of-scope crate is the reference check inside
    ``assert_writable`` and not a lucky 404 from the fetch.
    """

    permission_classes = [PermissionRequired]
    permission_key = "widget.update"

    def post(self, request, pk):
        try:
            widget = widget_get_writable(fetched_by=request.user, pk=pk)
        except Widget.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            crate = Crate.objects.get(pk=request.data.get("crate"))
        except (Crate.DoesNotExist, ValueError, TypeError):
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            moved = widget_transfer(
                actor=request.user, widgets=[widget], to_crate=crate
            )
        except WidgetLocked as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response({"moved": moved})
