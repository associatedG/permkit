"""permkit exception hierarchy.

The split mirrors the design rule that *permission* and *invariant* are
different kinds of "no":

* ``PermissionDenied`` — you, specifically, may not.  Maps to HTTP 403.
* Domain invariants ("nobody may edit a completed order") are **not** raised
  from here.  They belong in the service layer and map to 409/422.

Configuration problems (unknown key, unknown block, bad params) are their own
branch so they can never be mistaken for an authorization outcome.
"""

from __future__ import annotations


class PermKitError(Exception):
    """Base class for everything permkit raises."""


class PermissionDenied(PermKitError):
    """The principal is not permitted to perform this action."""

    def __init__(self, key: str, detail: str = ""):
        self.key = key
        self.detail = detail
        super().__init__(detail or f"Permission denied for key {key!r}.")


class ConfigurationError(PermKitError):
    """Base for registry/config problems. Never an authorization outcome."""


class UnknownKey(ConfigurationError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(
            f"Permission key {key!r} is not registered. "
            f"Register it with permkit.register_key() before use."
        )


class UnknownBlock(ConfigurationError):
    def __init__(self, block_id: str):
        self.block_id = block_id
        super().__init__(
            f"Object block {block_id!r} is not registered. "
            f"Register it with @permkit.register_block() before use."
        )


class InvalidParams(ConfigurationError):
    """A grant supplied params that do not match the block's declared schema."""


class DuplicateRegistration(ConfigurationError):
    """Two registrations claimed the same id."""
