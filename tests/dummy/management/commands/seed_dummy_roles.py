"""Compose the dummy domain's permissions the way a person would.

Not one permission per role. The point of the abstract role is that a
permission is a *job*, not a person: "see every widget" is one permission that
both the admin and the viewer hold, and the admin is distinguished by holding
several more. A seeder that produced one bundle per role would demonstrate the
tables while hiding the idea.

Idempotent, so it can be re-run after ``permkit_sync``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from permkit.catalogue.models import (
    RegisteredEndpoint,
    RegisteredFieldGroup,
    RegisteredFilter,
    RegisteredObject,
)
from permkit.catalogue.sync import sync_catalogue
from permkit.models import (
    Permission,
    PermissionEndpoint,
    PermissionFieldGrant,
    PermissionRule,
    PermissionRuleCondition,
    Role,
    RolePermission,
)

from tests.dummy.models import Crate, User, Widget

#: key → (name, description)
PERMISSIONS = {
    "widget-browse-all": (
        "Browse every widget",
        "Read access to the whole table. Prices are a separate permission.",
    ),
    "widget-browse-own-warehouse": (
        "Browse my warehouse's widgets",
        "Read access limited to the warehouse on the acting user's record.",
    ),
    "widget-edit-all": ("Edit every widget", "Write access to the whole table."),
    "widget-edit-assigned": (
        "Edit widgets assigned to me",
        "Write access to rows that are both in my warehouse and assigned to me. "
        "Narrower than what the same role can read, which is the point of "
        "separating the view and update keys.",
    ),
    "widget-see-prices": (
        "See widget prices",
        "Adds secret_price to what is returned. Without it the field is "
        "stripped from the payload rather than refused.",
    ),
    "widget-set-prices": (
        "Set widget prices on edit",
        "Writing a price on an existing row. Deliberately separate from "
        "setting one at creation.",
    ),
    "widget-create": ("Create widgets", "Reaching the creation endpoint."),
    "crate-browse": (
        "Browse crates",
        "Also decides which crates a widget may be filed into: the reference "
        "check on widget.crate resolves through this same key.",
    ),
}

#: role key → (label, description, permissions held)
ROLES = {
    "w_admin": (
        "Administrator",
        "Everything, prices included.",
        [
            "widget-browse-all",
            "widget-edit-all",
            "widget-see-prices",
            "widget-set-prices",
            "widget-create",
            "crate-browse",
        ],
    ),
    "w_keeper": (
        "Warehouse keeper",
        "Reads their own warehouse; edits only what is also assigned to them. "
        "Never sees prices.",
        ["widget-browse-own-warehouse", "widget-edit-assigned", "crate-browse"],
    ),
    "w_viewer": (
        "Viewer",
        "Every row, no prices.",
        ["widget-browse-all"],
    ),
    "w_outsider": (
        "Outsider",
        "Holds a role, holds no permissions. Exists to prove that zero grants "
        "denies rather than opening the table.",
        [],
    ),
}


class Command(BaseCommand):
    help = "Compose the dummy domain's roles, permissions and sample rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--superuser",
            action="store_true",
            help=(
                "Also create a demo login (admin/admin) for the local server. "
                "Never run this anywhere reachable."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Composition points at the catalogue by foreign key, so there has to
        # be a catalogue first.
        sync_catalogue()

        permissions = self._permissions()
        self._compose(permissions)
        self._assign(permissions)
        self._sample_rows()

        if options["superuser"]:
            self._demo_login()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{Role.objects.count()} roles, "
                f"{Permission.objects.count()} permissions, "
                f"{PermissionRule.objects.count()} rules, "
                f"{User.objects.count()} users."
            )
        )
        self.stdout.write("\nOpen /admin/permkit/permission/ to compose,")
        self.stdout.write("     /admin/permkit/permission/preview/ to ask why.")

    # -- composition ------------------------------------------------------

    def _permissions(self) -> dict[str, Permission]:
        out = {}
        for key, (name, description) in PERMISSIONS.items():
            permission, _ = Permission.objects.update_or_create(
                key=key, defaults={"name": name, "description": description}
            )
            out[key] = permission
        return out

    def _rule(self, permission, object_key, endpoint_key, *, label, filters=()):
        rule, _ = PermissionRule.objects.get_or_create(
            permission=permission,
            object=RegisteredObject.objects.get(key=object_key),
            endpoint_key=endpoint_key,
            defaults={"label": label, "order": 0},
        )
        for order, filter_key in enumerate(filters):
            PermissionRuleCondition.objects.get_or_create(
                rule=rule,
                filter=RegisteredFilter.objects.get(key=filter_key),
                defaults={"order": order},
            )
        return rule

    def _endpoint(self, permission, key):
        PermissionEndpoint.objects.get_or_create(
            permission=permission, endpoint=RegisteredEndpoint.objects.get(key=key)
        )

    def _field(self, permission, object_key, group_key, endpoint_key):
        PermissionFieldGrant.objects.get_or_create(
            permission=permission,
            field_group=RegisteredFieldGroup.objects.get(
                object__key=object_key, key=group_key
            ),
            endpoint_key=endpoint_key,
        )

    def _compose(self, p: dict[str, Permission]) -> None:
        self._endpoint(p["widget-browse-all"], "widget.view")
        self._rule(
            p["widget-browse-all"], "widget", "view", label="every row"
        )

        self._endpoint(p["widget-browse-own-warehouse"], "widget.view")
        self._rule(
            p["widget-browse-own-warehouse"],
            "widget",
            "view",
            label="in my warehouse",
            filters=["widget.warehouse"],
        )

        self._endpoint(p["widget-edit-all"], "widget.update")
        self._rule(p["widget-edit-all"], "widget", "update", label="every row")

        self._endpoint(p["widget-edit-assigned"], "widget.update")
        # Two conditions on ONE rule, so they intersect: in my warehouse AND
        # assigned to me. Two rules would have unioned them, which is a much
        # wider grant and the easiest mistake to make in this model.
        self._rule(
            p["widget-edit-assigned"],
            "widget",
            "update",
            label="in my warehouse and assigned to me",
            filters=["widget.warehouse", "widget.assigned"],
        )

        self._field(p["widget-see-prices"], "widget", "money", "view")
        self._field(p["widget-set-prices"], "widget", "money", "update")

        self._endpoint(p["widget-create"], "widget.create")

        self._endpoint(p["crate-browse"], "crate.view")
        self._rule(p["crate-browse"], "crate", "view", label="every crate")

    def _assign(self, permissions: dict[str, Permission]) -> None:
        for key, (label, description, held) in ROLES.items():
            role, _ = Role.objects.update_or_create(
                key=key, defaults={"label": label, "description": description}
            )
            for permission_key in held:
                RolePermission.objects.get_or_create(
                    role=role, permission=permissions[permission_key]
                )

    # -- something to look at ---------------------------------------------

    def _sample_rows(self) -> None:
        crate, _ = Crate.objects.get_or_create(name="crate-1")
        Crate.objects.get_or_create(name="crate-2")

        people = {
            "demo_admin": ("w_admin", ""),
            "demo_keeper_1": ("w_keeper", "KHO_1"),
            "demo_keeper_2": ("w_keeper", "KHO_2"),
            "demo_viewer": ("w_viewer", ""),
            "demo_outsider": ("w_outsider", ""),
        }
        users = {}
        for username, (role, warehouse) in people.items():
            user, created = User.objects.get_or_create(
                username=username, defaults={"role": role, "warehouse": warehouse}
            )
            if created:
                user.set_unusable_password()
                user.save()
            users[username] = user

        owner = users["demo_admin"]
        keeper = users["demo_keeper_1"]
        for name, warehouse, assignee, status in (
            ("kho1-assigned", "KHO_1", keeper, Widget.Status.ACTIVE),
            ("kho1-unassigned", "KHO_1", None, Widget.Status.ACTIVE),
            ("kho1-locked", "KHO_1", keeper, Widget.Status.LOCKED),
            ("kho2-assigned", "KHO_2", users["demo_keeper_2"], Widget.Status.ACTIVE),
        ):
            Widget.objects.get_or_create(
                name=name,
                defaults={
                    "warehouse": warehouse,
                    "owner": owner,
                    "assignee": assignee,
                    "status": status,
                    "secret_price": 100,
                    "crate": crate,
                },
            )

    def _demo_login(self) -> None:
        user, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "role": "w_admin"},
        )
        user.is_staff = user.is_superuser = True
        user.set_password("admin")
        user.save()
        self.stdout.write(
            self.style.WARNING(
                "\nDemo login created: admin / admin — local use only. "
                "This account is a superuser, and permkit's superuser bypass "
                "means it passes every check regardless of what you compose. "
                "Use the preview screen with demo_keeper_1 to see real rules."
            )
        )
