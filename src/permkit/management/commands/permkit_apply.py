"""``permkit_apply`` — put a permission spec into the database.

    python manage.py permkit_apply permissions/baseline.py
    python manage.py permkit_apply myapp.permissions.baseline --check

Idempotent, so it belongs in a deploy right after ``permkit_sync``. See
:mod:`permkit.spec` for the file's shape and for exactly what it manages.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from permkit.exceptions import PermKitError
from permkit.spec import apply_spec


def _load(target: str):
    """Import a spec from a file path or a dotted module path.

    Both, because the two live in different places: a spec kept beside the app
    is an ordinary module, and one written for a single run is a file sitting
    wherever it was written.
    """
    path = Path(target)
    if path.suffix == ".py":
        if not path.exists():
            raise CommandError(f"No such spec file: {target}")
        name = f"_permkit_spec_{path.stem}"
        loader = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(loader)
        sys.modules[name] = module
        loader.loader.exec_module(module)
        return module

    try:
        return importlib.import_module(target)
    except ImportError as exc:
        raise CommandError(
            f"Could not load spec {target!r}: {exc}. Give a path to a .py file "
            f"or an importable dotted module."
        ) from None


class Command(BaseCommand):
    help = "Apply a permission spec file to the database."

    def add_arguments(self, parser):
        parser.add_argument("spec", help="Path to a .py spec, or a dotted module path.")
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Report what would change and write nothing. Exits non-zero if "
                "the database is out of step with the spec."
            ),
        )

    def handle(self, *args, **options):
        module = _load(options["spec"])
        check = options["check"]

        try:
            report = apply_spec(module, dry_run=check)
        except PermKitError as exc:
            # A spec error is a configuration mistake, not a crash: print the
            # sentence, not a traceback the reader has to excavate.
            raise CommandError(str(exc)) from None

        for label, keys, style in (
            ("created", report.created, self.style.SUCCESS),
            ("updated", report.updated, self.style.WARNING),
        ):
            for key in keys:
                self.stdout.write(style(f"  {label:9} {key}"))
        for key in report.unchanged:
            self.stdout.write(f"  unchanged {key}")
        for key in report.roles:
            self.stdout.write(self.style.SUCCESS(f"  new role  {key}"))
        if report.bindings:
            self.stdout.write(
                self.style.SUCCESS(f"  {report.bindings} new role binding(s)")
            )

        if not report.changed:
            self.stdout.write("\nalready applied")
        if check and report.changed:
            self.stdout.write(self.style.WARNING("\n(--check: nothing was written)"))
            raise CommandError("The database is out of step with the spec.")
