from __future__ import annotations

from django.urls import path

from .views import WidgetDetailApi, WidgetListApi, WidgetUpdateApi

urlpatterns = [
    path("widgets/", WidgetListApi.as_view(), name="widget-list"),
    path("widgets/<int:pk>/", WidgetDetailApi.as_view(), name="widget-detail"),
    path("widgets/<int:pk>/update/", WidgetUpdateApi.as_view(), name="widget-update"),
]
