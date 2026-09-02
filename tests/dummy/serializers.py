"""Serializers for the dummy domain — used to prove the field tier.

``CrateSerializer`` nests ``WidgetSerializer`` so field stripping can be
verified *through* a nested serializer, not merely at the top level.  A field
tier that only works on the outermost object is a leak waiting to happen.
"""

from __future__ import annotations

from rest_framework import serializers

from permkit.drf import FieldPermissionMixin

from .models import Crate, Widget


class WidgetSerializer(FieldPermissionMixin, serializers.ModelSerializer):
    permission_object = "widget"
    permission_fields = {
        "money": ["secret_price"],
    }
    permission_references = {"crate": "crate.view"}

    class Meta:
        model = Widget
        fields = ["id", "name", "secret_price", "notes", "status", "warehouse"]


class CrateSerializer(serializers.ModelSerializer):
    widgets = WidgetSerializer(many=True, read_only=True)

    class Meta:
        model = Crate
        fields = ["id", "name", "widgets"]
