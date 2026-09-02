"""One route per read or write path. See ``PERMISSIONS.md``."""

from __future__ import annotations

from django.urls import path

from .views import (
    CrateListApi,
    WidgetCreateApi,
    WidgetDetailApi,
    WidgetListApi,
    WidgetTransferApi,
    WidgetUpdateApi,
    WidgetWritableListApi,
)

urlpatterns = [
    # read
    path("widgets/", WidgetListApi.as_view(), name="widget-list"),
    path("widgets/<int:pk>/", WidgetDetailApi.as_view(), name="widget-detail"),
    path("crates/", CrateListApi.as_view(), name="crate-list"),
    path(
        "widgets/writable/",
        WidgetWritableListApi.as_view(),
        name="widget-writable-list",
    ),
    # write
    path("widgets/create/", WidgetCreateApi.as_view(), name="widget-create"),
    path("widgets/<int:pk>/update/", WidgetUpdateApi.as_view(), name="widget-update"),
    path(
        "widgets/<int:pk>/transfer/",
        WidgetTransferApi.as_view(),
        name="widget-transfer",
    ),
]
