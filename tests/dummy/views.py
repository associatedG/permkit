"""DRF views for the dummy domain — one class per operation.

Two integration styles, both real:

* **List and detail call the selector.** One scoping path for every caller —
  HTTP, service, task, command — so the guarantee that a declared site must
  apply its filters covers the API too.  This is the pattern to prefer.
* **Update uses ``ScopedQuerysetMixin``.** For projects with generic views and
  no selector layer, which is most existing Django code.  It reads the model
  directly, so nothing stops a future edit dropping the mixin and serving
  every row — the reason the coverage check exists.
"""

from __future__ import annotations

from rest_framework import generics

from permkit import api_action
from permkit.drf import PermissionRequired, ScopedQuerysetMixin

from .models import Widget
from .selectors import widget_list
from .serializers import WidgetSerializer


@api_action("widget.view", label="List widgets")
class WidgetListApi(generics.ListAPIView):
    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]

    def get_queryset(self):
        return widget_list(fetched_by=self.request.user)


@api_action("widget.view")
class WidgetDetailApi(generics.RetrieveAPIView):
    """Enforces the same action as the list view.

    A list and its detail are one permission, so both name ``widget.view``.
    The list view declared the label; this one simply registers as another
    place the action is enforced.
    """

    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]

    def get_queryset(self):
        return widget_list(fetched_by=self.request.user)


@api_action("widget.update", label="Update a widget", mode="WRITE")
class WidgetUpdateApi(ScopedQuerysetMixin, generics.UpdateAPIView):
    """One key covers all three view-level tiers.

    ``permission_key`` alone drives the endpoint check, the queryset scoping
    and the object check — the mixin and the permission class both fall back
    to it.  ``read_key`` / ``object_key`` exist only for the rarer case where
    a view needs them to differ.

    Because that key is ``widget.update``, this view's queryset is narrowed by
    the *write* scope: a row the user may read but not edit is not here at
    all, so the mutation route 404s rather than 403s.
    """

    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    permission_classes = [PermissionRequired]
