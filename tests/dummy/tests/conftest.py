"""Fixtures for the dummy service's own tests.

The role/grant fixtures come from the project-level ``tests/conftest.py``,
which pytest applies down the tree.  What is added here is specific to
driving the dummy app's HTTP surface.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from ..models import Crate


@pytest.fixture
def api():
    """An unauthenticated client; call ``as_(user)`` to bind a principal."""

    class _Api(APIClient):
        def as_(self, user):
            self.force_authenticate(user=user)
            return self

    return _Api()


@pytest.fixture
def crate_one(widgets) -> Crate:
    """The crate the fixture widgets already sit in."""
    return Crate.objects.get(name="crate-1")


@pytest.fixture
def far_crate(db) -> Crate:
    """A crate no restricted role can see."""
    return Crate.objects.create(name="far-crate")


@pytest.fixture
def filer(store, make_user):
    """Edits every widget, but may only see ``crate-1``.

    Exists to isolate the reference check: nothing about this role's widget
    grants explains a refusal, so a denial can only come from ``crate.view``.
    """
    store.grant_endpoint("filer", "widget.update")
    store.grant_object("filer", "widget.update", name="filer-widgets-all")
    store.grant_endpoint("filer", "crate.view")
    store.grant_object(
        "filer",
        "crate.view",
        name="filer-crate-one",
        conditions=[{"condition": "crate.named", "params": {"names": ["crate-1"]}}],
    )
    return make_user(role="filer")
