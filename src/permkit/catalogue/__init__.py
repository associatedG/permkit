"""Tier 1 — the catalogue: the code-side registry, published as rows.

The registry lives in memory and is assembled at import time, which is fine
for enforcement and useless for composition: an admin UI cannot enumerate a
Python object graph, and a grant cannot hold a foreign key into one.  So the
declarations become data, and everything above this tier composes from the
tables rather than from the registry.

The tables are a *projection*, never a source of truth.  Nothing here is
edited by hand; ``permkit_sync`` rewrites it from code on every deploy.
"""

from __future__ import annotations

__all__ = ["sync_catalogue", "SyncReport", "Problem", "load_declarations"]


def __getattr__(name: str):
    # Lazy, because this package is reachable from ``permkit.models`` during
    # app loading, when importing the sync machinery would be premature.
    if name in ("sync_catalogue", "SyncReport", "Problem"):
        from . import sync

        return getattr(sync, name)
    if name == "load_declarations":
        from .loading import load_declarations

        return load_declarations
    raise AttributeError(name)
