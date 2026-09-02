from __future__ import annotations

import pytest

from permkit.conf import reset_policy, set_policy
from permkit.principals import AttributeRoleResolver
from permkit.resolver import Policy
from permkit.store import MemoryStore

from .dummy.models import Crate, User, Widget

ADMIN = "w_admin"
EDITOR = "w_editor"
VIEWER = "w_viewer"
KEEPER = "w_keeper"
OUTSIDER = "w_outsider"


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def policy(store):
    """Install a Policy backed by the in-memory store for the duration of a test."""
    p = Policy(store=store, principals=AttributeRoleResolver("role"))
    set_policy(p)
    yield p
    reset_policy()


@pytest.fixture
def make_user(db):
    counter = {"n": 0}

    def _make(role: str = "", warehouse: str = "", **kwargs) -> User:
        counter["n"] += 1
        return User.objects.create_user(
            username=kwargs.pop("username", f"u{counter['n']}"),
            password="x",
            role=role,
            warehouse=warehouse,
            **kwargs,
        )

    return _make


@pytest.fixture
def admin_user(make_user):
    return make_user(role=ADMIN)


@pytest.fixture
def keeper_kho1(make_user):
    return make_user(role=KEEPER, warehouse="KHO_1")


@pytest.fixture
def keeper_kho2(make_user):
    return make_user(role=KEEPER, warehouse="KHO_2")


@pytest.fixture
def viewer(make_user):
    return make_user(role=VIEWER)


@pytest.fixture
def outsider(make_user):
    """A user holding a role with no grants at all."""
    return make_user(role=OUTSIDER)


@pytest.fixture
def widgets(db, admin_user, keeper_kho1):
    """A spread covering both warehouses, assignment and the locked state."""
    crate = Crate.objects.create(name="crate-1")
    return {
        "kho1_assigned": Widget.objects.create(
            name="kho1-assigned",
            warehouse="KHO_1",
            owner=admin_user,
            assignee=keeper_kho1,
            secret_price=100,
            crate=crate,
        ),
        "kho1_unassigned": Widget.objects.create(
            name="kho1-unassigned",
            warehouse="KHO_1",
            owner=admin_user,
            secret_price=200,
            crate=crate,
        ),
        "kho2": Widget.objects.create(
            name="kho2",
            warehouse="KHO_2",
            owner=admin_user,
            secret_price=300,
        ),
        "kho1_locked": Widget.objects.create(
            name="kho1-locked",
            warehouse="KHO_1",
            owner=admin_user,
            assignee=keeper_kho1,
            status=Widget.Status.LOCKED,
            secret_price=400,
        ),
    }


@pytest.fixture
def grants(store):
    """The standard rule set the suite exercises.

    Mirrors the real shape found in the target codebase: an admin who sees
    everything including prices, a warehouse keeper who sees only their own
    warehouse and may edit only what is *also* assigned to them, and a viewer
    who sees every row but no prices.
    """
    # -- admin: everything, prices included -----------------------------
    store.grant_endpoint(ADMIN, "widget.view")
    store.grant_endpoint(ADMIN, "widget.update")
    store.grant_endpoint(ADMIN, "widget.create")
    store.grant_object(ADMIN, "widget.view", name="admin-view-all")
    store.grant_object(ADMIN, "widget.update", name="admin-update-all")
    store.grant_field(
        ADMIN, "widget.view", name="admin-read-price", allowed_fields=["secret_price"]
    )
    store.grant_field(
        ADMIN,
        "widget.update",
        name="admin-write-all",
        allowed_fields=["secret_price", "notes", "status", "crate"],
    )
    # A separate grant, because ``widget.create`` is a separate key — an
    # update grant deliberately does not carry over to creation.
    store.grant_field(
        ADMIN,
        "widget.create",
        name="admin-create-all",
        allowed_fields=["secret_price", "notes"],
    )

    # Writing the ``crate`` FK is governed by ``crate.view``, so a role that
    # may move widgets needs read scope on the destination too.
    store.grant_endpoint(ADMIN, "crate.view")
    store.grant_object(ADMIN, "crate.view", name="admin-crates-all")

    # -- keeper: own warehouse to read; own warehouse AND assigned to write
    store.grant_endpoint(KEEPER, "widget.view")
    store.grant_endpoint(KEEPER, "widget.update")
    store.grant_endpoint(KEEPER, "crate.view")
    store.grant_object(KEEPER, "crate.view", name="keeper-crates-all")
    store.grant_object(
        KEEPER,
        "widget.view",
        name="keeper-view-own-warehouse",
        conditions=[
            {"condition": "widget.warehouse"}
        ],
    )
    store.grant_object(
        KEEPER,
        "widget.update",
        name="keeper-update-own-warehouse-and-assigned",
        conditions=[
            {"condition": "widget.warehouse"},
            {"condition": "widget.assigned"},
        ],
    )
    # May write notes, but never sees or writes secret_price.
    store.grant_field(
        KEEPER,
        "widget.update",
        name="keeper-write-notes",
        allowed_fields=["notes", "crate"],
    )

    # -- viewer: every row, no prices -----------------------------------
    store.grant_endpoint(VIEWER, "widget.view")
    store.grant_object(VIEWER, "widget.view", name="viewer-view-all")

    # -- outsider: deliberately nothing ---------------------------------
    return store
