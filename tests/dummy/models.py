"""The synthetic domain permkit is proved against.

Deliberately *not* a real domain.  If a rule cannot be expressed against
``Widget``, that is evidence the abstraction is wrong — not a reason to reach
for a production model.

``warehouse`` lives on the user rather than being inferred from the role name,
which is what lets a second keeper join a warehouse without a code change.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class Crate(models.Model):
    """A parent, so field stripping can be proved through a nested serializer.

    Doubles as the stand-in for a "product line / business type" — the
    many-to-many scope a person may be responsible for several of.
    """

    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    role = models.CharField(max_length=100, blank=True)
    warehouse = models.CharField(max_length=50, blank=True)
    #: The many-to-many scope: "the lines I am responsible for".
    crates = models.ManyToManyField(Crate, blank=True, related_name="responsibles")


class Widget(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        ACTIVE = "ACTIVE"
        LOCKED = "LOCKED"

    name = models.CharField(max_length=100)
    secret_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    warehouse = models.CharField(max_length=50, blank=True)
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_widgets"
    )
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_widgets",
    )
    crate = models.ForeignKey(
        Crate, on_delete=models.CASCADE, null=True, blank=True, related_name="widgets"
    )
    #: A to-many relation, so ``.distinct()`` handling can be exercised.
    watchers = models.ManyToManyField(User, blank=True, related_name="watched_widgets")

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name
