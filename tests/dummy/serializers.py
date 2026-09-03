"""Serializers for the dummy domain — where the field tier is declared.

``permission_fields`` names the groups an administrator can grant.
``permission_references`` names the foreign keys whose *target row* is
governed by another key.  Both are declared here because the serializer is
where the payload's shape is known.

``CrateSerializer`` nests ``WidgetSerializer`` so field stripping can be
verified *through* a nested serializer, not merely at the top level.  A field
tier that only works on the outermost object is a leak waiting to happen.
"""

from __future__ import annotations

from rest_framework import serializers

from permkit.drf import FieldPermissionMixin

from .models import Crate, Widget


class WidgetSerializer(FieldPermissionMixin, serializers.ModelSerializer):
    """Read with ``widget.view``, write with ``widget.update``."""

    permission_object = "widget"
    permission_fields = {
        "money": ["secret_price"],
    }
    # ``crate`` must be serialized for this to have anything to fire on: a
    # governed FK that never reaches ``attrs`` leaves the check inert.
    permission_references = {"crate": "crate.view"}

    class Meta:
        model = Widget
        fields = [
            "id",
            "name",
            "secret_price",
            "notes",
            "status",
            "warehouse",
            "crate",
        ]


class WidgetCreateSerializer(WidgetSerializer):
    """Same fields, validated against ``widget.create``.

    Creation is a different key from update, so it carries different field
    grants — an admin who may edit prices on an existing row has not thereby
    been granted the right to set one on a new row.  Only ``write_endpoint``
    changes; the mixin derives the key from it.
    """

    write_endpoint = "create"


class CrateSerializer(serializers.ModelSerializer):
    """The crate picker, and the parent used to prove nested stripping."""

    widgets = WidgetSerializer(many=True, read_only=True)

    class Meta:
        model = Crate
        fields = ["id", "name", "widgets"]
