"""The service layer — including the paths no DRF hook reaches.

Celery tasks, management commands and admin actions enter the domain through
services, not views.  Guarding them here is what makes coverage complete
rather than HTTP-shaped.
"""

from __future__ import annotations

import pytest

from permkit import PermissionDenied

from .dummy.models import Widget
from .dummy.services import widget_create, widget_update

pytestmark = pytest.mark.django_db


def test_create_requires_the_endpoint_grant(policy, grants, admin_user):
    widget = widget_create(actor=admin_user, name="fresh", secret_price=5)
    assert Widget.objects.filter(pk=widget.pk).exists()
    assert widget.owner == admin_user


def test_create_is_denied_without_the_grant(policy, grants, keeper_kho1):
    """The keeper may update, but was never granted ``widget.create``."""
    with pytest.raises(PermissionDenied):
        widget_create(actor=keeper_kho1, name="nope")

    assert not Widget.objects.filter(name="nope").exists()


def test_create_enforces_write_field_permissions(policy, store, grants, make_user):
    """A create has no rows, and still carries a field tier."""
    store.grant_endpoint("creator", "widget.create")
    store.grant_field(
        "creator", "widget.create", name="creator-notes", allowed_fields=["notes"]
    )
    actor = make_user(role="creator")

    widget_create(actor=actor, name="ok", notes="fine")

    with pytest.raises(PermissionDenied):
        widget_create(actor=actor, name="bad", secret_price=99)

    assert not Widget.objects.filter(name="bad").exists()


def test_guarded_service_rejects_a_missing_actor(policy, grants, widgets):
    """A guarded service called without its actor is a programming error.

    Silently treating a missing actor as anonymous would be a quiet
    downgrade to "denied" that masks the bug.
    """
    with pytest.raises(TypeError, match="actor"):
        widget_update(widget=widgets["kho1_assigned"], notes="x")

    with pytest.raises(TypeError, match="actor"):
        widget_create(name="x")


def test_guarded_service_rejects_a_missing_object(policy, grants, admin_user):
    with pytest.raises(TypeError, match="widget"):
        widget_update(actor=admin_user, notes="x")


def test_service_guard_covers_non_http_callers(policy, grants, widgets, outsider):
    """No request, no view — the guard still applies."""
    with pytest.raises(PermissionDenied):
        widget_update(actor=outsider, widget=widgets["kho1_assigned"], notes="x")
