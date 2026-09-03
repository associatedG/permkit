"""``permkit_roles`` — find role names that do not line up.

A user's role reaches permkit as a *string*, matched by name against the
``Role`` table. That is what keeps permkit out of your user model, and it is
also the one place in the system where a typo is silent: a user whose role is
``w_keper`` matches no row, is granted nothing, and looks exactly like a user
who was deliberately given nothing.

Every other tier already refuses to fail quietly — an unregistered key raises,
a filter with no scope point fails the sync. This is the same idea applied to
the last unguarded edge.
"""

from __future__ import annotations

from collections import Counter

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from permkit.conf import get_policy
from permkit.models import Role


class Command(BaseCommand):
    help = "Report user roles that match no Role row, and Roles nobody holds."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Also fail on roles nobody holds and roles granting nothing.",
        )

    # -- gathering --------------------------------------------------------

    def _held_roles(self, User) -> Counter:
        """Count role strings actually in use, however the project stores them.

        Fast path for the ordinary case — a plain column — because that is one
        query instead of one per user. Anything else goes through the resolver
        itself, which is the only thing that knows how to read a many-to-many
        or a claim.
        """
        resolver = get_policy().principals
        attribute = getattr(resolver, "attribute", None)

        if attribute:
            try:
                field = User._meta.get_field(attribute)
            except FieldDoesNotExist:
                field = None
            if field is not None and not field.is_relation:
                counts = Counter()
                rows = (
                    User.objects.values(attribute)
                    .annotate(n=Count("pk"))
                    .values_list(attribute, "n")
                )
                for raw, total in rows:
                    # Normalised the same way the resolver would, so "W_Keeper"
                    # and "w_keeper" are not reported as two different problems.
                    counts[(raw or "").strip().lower()] += total
                return counts

        self.stdout.write(
            self.style.WARNING(
                f"  {type(resolver).__name__} does not read a plain column, so "
                f"every user is being read one at a time."
            )
        )
        counts = Counter()
        for user in User.objects.iterator():
            roles = resolver.roles_for(user)
            if not roles:
                counts[""] += 1
            for role in roles:
                counts[role] += 1
        return counts

    # -- run --------------------------------------------------------------

    def handle(self, *args, **options):
        User = get_user_model()
        held = self._held_roles(User)
        known = {r.key: r for r in Role.objects.prefetch_related("permissions")}

        unmatched = {k: n for k, n in held.items() if k and k not in known}
        roleless = held.get("", 0)
        unheld = [k for k in known if k not in held]
        empty = [k for k, r in known.items() if not r.permissions.all()]

        self.stdout.write(f"\n  {len(known)} role(s) defined, {sum(held.values())} user(s) scanned\n")

        for key in sorted(known):
            n = held.get(key, 0)
            count = len(known[key].permissions.all())
            line = f"  {key:<22} {n:>5} user(s)   {count} permission(s)"
            self.stdout.write(self.style.SUCCESS(line) if n and count else line)

        if unmatched:
            self.stdout.write("")
            for key, n in sorted(unmatched.items()):
                self.stdout.write(
                    self.style.ERROR(
                        f"  {key!r} is held by {n} user(s) but matches no Role. "
                        f"They are granted nothing, and nothing says so."
                    )
                )

        if roleless:
            self.stdout.write("")
            self.stdout.write(
                f"  {roleless} user(s) have no role at all — denied everything, "
                f"which may well be intended."
            )

        for key in sorted(unheld):
            self.stdout.write(
                self.style.WARNING(f"  Role {key!r} exists but nobody holds it.")
            )
        for key in sorted(empty):
            self.stdout.write(
                self.style.WARNING(f"  Role {key!r} holds no permissions.")
            )

        failures = list(unmatched)
        if options["strict"]:
            failures += unheld + empty
        if failures:
            raise CommandError(
                f"{len(failures)} role problem(s); see above. A role string that "
                f"matches no Role row is indistinguishable from a deliberate deny."
            )
