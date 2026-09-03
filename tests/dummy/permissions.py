"""The dummy domain's baseline permissions, as a spec.

The reference example for what ``permkit-grant`` tells people to write. It is
applied by ``seed_dummy_roles`` and by the test suite, so if the format drifts
from the documentation the tests notice.

Note the shape: one permission per *job*, several of them held by more than one
role. "Browse every widget" is a thing the administrator and the viewer both
have; the administrator is distinguished by holding five more. A file with one
bundle per role would demonstrate the tables and hide the idea.
"""

from __future__ import annotations

PERMISSIONS = {
    "widget-browse-all": {
        "name": "Browse every widget",
        "description": "Read access to the whole table. Prices are a separate permission.",
        "endpoints": ["widget.view"],
        "rules": [{"key": "widget.view", "label": "every row"}],
    },
    "widget-browse-own-warehouse": {
        "name": "Browse my warehouse's widgets",
        "description": "Read access limited to the warehouse on the acting user's record.",
        "endpoints": ["widget.view"],
        "rules": [
            {
                "key": "widget.view",
                "label": "in my warehouse",
                "conditions": [{"filter": "widget.warehouse"}],
            }
        ],
    },
    "widget-edit-all": {
        "name": "Edit every widget",
        "description": "Write access to the whole table.",
        "endpoints": ["widget.update"],
        "rules": [{"key": "widget.update", "label": "every row"}],
    },
    "widget-edit-assigned": {
        "name": "Edit widgets assigned to me",
        "description": (
            "Write access to rows both in my warehouse and assigned to me. "
            "Narrower than what the same role can read, which is the point of "
            "separating the view and update keys."
        ),
        "endpoints": ["widget.update"],
        "rules": [
            {
                "key": "widget.update",
                "label": "in my warehouse and assigned to me",
                # Two conditions on ONE rule, so they intersect. Two rules
                # would have unioned them — a much wider grant, and the
                # easiest mistake to make in this model.
                "conditions": [
                    {"filter": "widget.warehouse"},
                    {"filter": "widget.assigned"},
                ],
            }
        ],
    },
    "widget-see-prices": {
        "name": "See widget prices",
        "description": (
            "Adds secret_price to what is returned. Without it the field is "
            "stripped from the payload rather than the request refused."
        ),
        "fields": [{"group": "widget.money", "endpoint": "view"}],
    },
    "widget-set-prices": {
        "name": "Set widget prices on edit",
        "description": "Deliberately separate from setting one at creation.",
        "fields": [{"group": "widget.money", "endpoint": "update"}],
    },
    "widget-create": {
        "name": "Create widgets",
        "description": "Reaching the creation endpoint.",
        "endpoints": ["widget.create"],
    },
    "crate-browse": {
        "name": "Browse crates",
        "description": (
            "Also decides which crates a widget may be filed into: the "
            "reference check on widget.crate resolves through this same key."
        ),
        "endpoints": ["crate.view"],
        "rules": [{"key": "crate.view", "label": "every crate"}],
    },
}

ROLES = {
    "w_admin": {
        "label": "Administrator",
        "description": "Everything, prices included.",
        "permissions": [
            "widget-browse-all",
            "widget-edit-all",
            "widget-see-prices",
            "widget-set-prices",
            "widget-create",
            "crate-browse",
        ],
    },
    "w_keeper": {
        "label": "Warehouse keeper",
        "description": (
            "Reads their own warehouse; edits only what is also assigned to "
            "them. Never sees prices."
        ),
        "permissions": [
            "widget-browse-own-warehouse",
            "widget-edit-assigned",
            "crate-browse",
        ],
    },
    "w_viewer": {
        "label": "Viewer",
        "description": "Every row, no prices.",
        "permissions": ["widget-browse-all"],
    },
    "w_outsider": {
        "label": "Outsider",
        "description": (
            "Holds a role, holds no permissions. Exists to prove that zero "
            "grants denies rather than opening the table."
        ),
        "permissions": [],
    },
}
