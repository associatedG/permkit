"""A real non-HTTP entrypoint.

Management commands, Celery tasks and admin actions reach the domain through
services, never through a view — so no DRF permission class ever runs for them.
This command exists so that claim is tested rather than asserted.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from tests.dummy.models import User
from tests.dummy.services import widget_bulk_annotate


class Command(BaseCommand):
    help = "Annotate every widget the given user is permitted to write."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--note", required=True)

    def handle(self, *args, **options):
        actor = User.objects.get(username=options["username"])
        updated = widget_bulk_annotate(actor=actor, note=options["note"])
        self.stdout.write(f"annotated {updated}")
