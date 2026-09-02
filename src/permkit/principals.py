"""How a user becomes a set of role ids.

This is a seam because consumers disagree: one project stores a single ``role``
string on the user, another will use a many-to-many, a third a JWT claim.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from .models import normalize_role


@runtime_checkable
class PrincipalResolver(Protocol):
    def roles_for(self, user) -> list[str]:
        """Return normalised role ids for ``user`` (may be empty)."""
        ...


class AttributeRoleResolver:
    """Reads roles off a user attribute.

    Handles both the single-role (``user.role == "manager"``) and multi-role
    (``user.roles == [...]``) shapes, since consumers differ.
    """

    def __init__(self, attribute: str = "role") -> None:
        self.attribute = attribute

    def roles_for(self, user) -> list[str]:
        if user is None:
            return []
        raw = getattr(user, self.attribute, None)
        if not raw:
            return []
        if isinstance(raw, str):
            values: Iterable[str] = [raw]
        elif hasattr(raw, "all"):  # a related manager
            values = [str(v) for v in raw.all()]
        else:
            values = [str(v) for v in raw]
        return [normalize_role(v) for v in values if v]
