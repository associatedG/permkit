"""``permkit_sync`` — publish what the code declares into the catalogue tables.

Run it on every deploy, after ``migrate``. It is idempotent, so running it
twice is a no-op, and it exits non-zero when the configuration in the database
has drifted from the declarations in the code — which is the point of running
it in CI as well as on deploy.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from permkit.catalogue.sync import SyncReport, sync_catalogue


class _Rollback(Exception):
    """Raised to unwind --check's transaction. Never escapes handle()."""


class Command(BaseCommand):
    help = "Sync the permkit catalogue tables from the code-side registry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Report what would change and write nothing. Exits non-zero if "
                "the catalogue is out of date, so CI can fail a branch that "
                "changed a declaration without syncing."
            ),
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Treat warnings as errors.",
        )
        parser.add_argument(
            "--no-load",
            action="store_true",
            help=(
                "Scrape the registry as it stands instead of forcing the "
                "declaration modules in first. Diagnostic only: it is how you "
                "see what an incomplete import would have published."
            ),
        )

    # -- output -----------------------------------------------------------

    def _render(self, report: SyncReport, *, check: bool, verbosity: int) -> None:
        self.stdout.write(f"loaded {report.load}")
        if verbosity >= 2:
            for module in report.load.modules:
                self.stdout.write(f"    {module}")
        self.stdout.write("")

        width = max(len(t.name) for t in report.tables)
        for table in report.tables:
            bits = []
            for count, word in (
                (table.created, "new"),
                (table.updated, "changed"),
                (table.revived, "revived"),
                (table.retired, "retired"),
            ):
                if count:
                    bits.append(f"{count} {word}")
            detail = f"  ({', '.join(bits)})" if bits else ""
            line = f"  {table.name:<{width}}  {table.live:>3} live{detail}"
            style = self.style.WARNING if table.changed else self.style.SUCCESS
            self.stdout.write(style(line))

        if not report.changed:
            self.stdout.write("")
            self.stdout.write("catalogue already up to date")

        for problem in report.problems:
            self.stdout.write("")
            style = (
                self.style.ERROR
                if problem.severity == "error"
                else self.style.WARNING
            )
            self.stdout.write(style(str(problem)))

        if check and report.changed:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("(--check: nothing was written)"))

    # -- run --------------------------------------------------------------

    def handle(self, *args, **options):
        check = options["check"]
        report = None

        try:
            with transaction.atomic():
                report = sync_catalogue(load=not options["no_load"])
                if check:
                    # Run the real write, then throw it away. Validation reads
                    # the tables this run wrote, so a dry run that skipped the
                    # write would report a different set of problems from the
                    # one it is standing in for.
                    raise _Rollback
        except _Rollback:
            pass

        self._render(report, check=check, verbosity=options["verbosity"])

        failures = list(report.errors)
        if options["strict"]:
            failures += report.warnings
        if failures:
            raise CommandError(
                f"{len(failures)} catalogue problem(s); see above. Each one is "
                f"a declaration or a grant that no longer means what it says."
            )
        if check and report.changed:
            raise CommandError(
                "The catalogue is out of date with the declarations in code. "
                "Run permkit_sync."
            )
