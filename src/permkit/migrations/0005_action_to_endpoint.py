"""Rename every "action" to "endpoint".

Hand-written rather than autodetected. ``makemigrations`` cannot tell a rename
from a drop-and-create without being asked interactively, and answering wrong
here would throw away every composed permission on a real install.

Constraints come off first: they name the columns being renamed, and a rename
under a live constraint fails on some backends.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("permkit", "0004_catalogue_verbose_names"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="permissionaction",
            name="permkit_unique_permission_action",
        ),
        migrations.RemoveConstraint(
            model_name="permissionfieldgrant",
            name="permkit_unique_permission_field_grant",
        ),
        migrations.RemoveConstraint(
            model_name="registeredscopepoint",
            name="permkit_unique_scope_point",
        ),

        migrations.RenameModel(
            old_name="RegisteredAction",
            new_name="RegisteredEndpoint",
        ),
        migrations.RenameModel(
            old_name="PermissionAction",
            new_name="PermissionEndpoint",
        ),

        migrations.RenameField(
            model_name="permissionendpoint",
            old_name="action",
            new_name="endpoint",
        ),
        migrations.RenameField(
            model_name="permissionrule",
            old_name="action_key",
            new_name="endpoint_key",
        ),
        migrations.RenameField(
            model_name="permissionfieldgrant",
            old_name="action_key",
            new_name="endpoint_key",
        ),
        migrations.RenameField(
            model_name="registeredscopepoint",
            old_name="action_key",
            new_name="endpoint_key",
        ),

        migrations.AlterModelOptions(
            name="permissionendpoint",
            options={"ordering": ("endpoint__key",)},
        ),
        migrations.AlterModelOptions(
            name="permissionfieldgrant",
            options={
                "ordering": (
                    "field_group__object__key",
                    "endpoint_key",
                    "field_group__key",
                )
            },
        ),
        migrations.AlterModelOptions(
            name="registeredscopepoint",
            options={
                "ordering": ("object__key", "endpoint_key", "target"),
                "verbose_name": "declared enforcement point",
                "verbose_name_plural": "declared enforcement points",
            },
        ),
        migrations.AlterModelOptions(
            name="registeredendpoint",
            options={
                "ordering": ("key",),
                "verbose_name": "declared endpoint",
                "verbose_name_plural": "declared endpoints",
            },
        ),

        # related_name="actions" -> "endpoints". State only, no SQL, but the
        # autodetector wants it recorded.
        migrations.AlterField(
            model_name="permissionendpoint",
            name="permission",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="endpoints",
                to="permkit.permission",
            ),
        ),

        migrations.AddConstraint(
            model_name="permissionendpoint",
            constraint=models.UniqueConstraint(
                fields=("permission", "endpoint"),
                name="permkit_unique_permission_endpoint",
            ),
        ),
        migrations.AddConstraint(
            model_name="permissionfieldgrant",
            constraint=models.UniqueConstraint(
                fields=("permission", "field_group", "endpoint_key"),
                name="permkit_unique_permission_field_grant",
            ),
        ),
        migrations.AddConstraint(
            model_name="registeredscopepoint",
            constraint=models.UniqueConstraint(
                fields=("object", "endpoint_key", "target"),
                name="permkit_unique_scope_point",
            ),
        ),
    ]
