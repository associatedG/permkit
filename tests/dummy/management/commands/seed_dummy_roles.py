"""Set the dummy domain up so the admin UI has something real in it.

The permissions themselves live in ``tests/dummy/permissions.py`` as a spec,
because that is what the ``permkit-grant`` skill tells people to write and the
reference implementation should not do something different. This command only
runs the documented sequence and then adds sample rows to look at.

Idempotent, so it can be re-run after ``permkit_sync``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from permkit.catalogue.sync import sync_catalogue
from permkit.models import Permission, PermissionRule, Role
from permkit.spec import apply_spec

from tests.dummy import permissions as spec
from tests.dummy.models import Crate, User, Widget


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
        report = apply_spec(spec)
        self._sample_rows()

        if options["superuser"]:
            self._demo_login()

        for key in report.created:
            self.stdout.write(self.style.SUCCESS(f"  created   {key}"))
        for key in report.updated:
            self.stdout.write(self.style.WARNING(f"  updated   {key}"))
        if not report.changed:
            self.stdout.write("  permissions already applied")

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
        user, _ = User.objects.get_or_create(
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
