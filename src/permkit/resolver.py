"""The resolver — every authorization decision in the library passes through here.

Layer 1 of the SDK.  Framework-agnostic on purpose: these take a user, a key
and (at most) a queryset or a dict.  No request, no view, no serializer.  That
is what lets the same rules cover DRF views, plain ``APIView``s, selectors,
services, management commands and Celery tasks, instead of only the code paths
that happen to inherit from a generic view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from django.db.models import Q, QuerySet

from .base import Context, KeySpec
from .cache import cached
from .exceptions import ConfigurationError, PermissionDenied
from .registry import Registry, registry as default_registry
from .store import GrantStore


#: Where a user's resolved roles are remembered. On the instance, so the cache
#: cannot outlive the object and there is nothing to invalidate.
_ROLE_CACHE_ATTR = "_permkit_roles"


def clear_role_cache(user) -> None:
    """Forget a user's cached roles, for a process that keeps one around.

    A web request never needs this. A long-running task that changes somebody's
    role and then keeps acting as them does.
    """
    try:
        user.__dict__.pop(_ROLE_CACHE_ATTR, None)
    except AttributeError:
        pass


class ScopeKind(Enum):
    DENY = "deny"
    ALL = "all"
    FILTERED = "filtered"


@dataclass(frozen=True)
class ScopeResult:
    """Outcome of resolving the object tier for one (user, key).

    ``DENY`` is a distinct state rather than an empty ``Q``.  In Django,
    ``Q()`` matches *everything*, so a user holding no grants at all would be
    handed the entire table.  Making deny its own kind puts that failure mode
    beyond reach.
    """

    kind: ScopeKind
    q: Q | None = None
    distinct: bool = False


@dataclass
class Trace:
    """Why a decision came out the way it did."""

    key: str
    roles: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    allowed: bool | None = None

    def add(self, line: str) -> None:
        self.lines.append(line)

    def __str__(self) -> str:
        head = f"key={self.key} roles={self.roles or '∅'} allowed={self.allowed}"
        body = "\n".join(f"  - {line}" for line in self.lines)
        return f"{head}\n{body}" if body else head


class Policy:
    def __init__(
        self,
        *,
        store: GrantStore,
        principals,
        registry: Registry | None = None,
        superuser_bypass: bool = True,
        context_builder=None,
        cache_roles: bool = True,
    ) -> None:
        self.store = store
        self.principals = principals
        self.registry = registry or default_registry
        self.superuser_bypass = superuser_bypass
        self.cache_roles = cache_roles
        self._context_builder = context_builder or (lambda user: Context(user=user))

    # -- helpers ----------------------------------------------------------

    def roles_for(self, user) -> list[str]:
        """The acting user's roles, resolved once per user object.

        Every tier asks this, and a list view asks it again for each row it
        strips fields on — so with a resolver that reads the database (roles in
        their own table, a profile, a claim) the same question was costing a
        query per check.

        The cache lives on the user *instance*, which is the honest scope: a
        request builds one, a task builds one, and the next one starts clean.
        That keeps revocation correct without an invalidation scheme — there is
        nothing to invalidate, because nothing outlives the object it hangs on.
        """
        if not self.cache_roles or user is None:
            return self.principals.roles_for(user)

        # __dict__ rather than getattr/setattr: no descriptor is triggered, and
        # an object that has no __dict__ (a __slots__ class) simply opts out
        # instead of raising.
        try:
            store = user.__dict__
        except AttributeError:
            return self.principals.roles_for(user)

        cached = store.get(_ROLE_CACHE_ATTR)
        if cached is not None:
            return cached

        roles = self.principals.roles_for(user)
        try:
            store[_ROLE_CACHE_ATTR] = roles
        except TypeError:  # a mappingproxy, e.g. a class rather than an instance
            pass
        return roles

    def _is_superuser(self, user) -> bool:
        return bool(self.superuser_bypass and getattr(user, "is_superuser", False))

    def _spec(self, key: str) -> KeySpec:
        return self.registry.key(key)  # raises UnknownKey

    # -- store access -----------------------------------------------------
    #
    # Routed through one place each so the grant cache covers every caller.
    # Outside a ``grant_cache()`` scope these are plain pass-throughs.

    def _endpoint_grant(self, roles: Sequence[str], key: str) -> bool:
        return cached(
            ("endpoint", tuple(roles), key),
            lambda: self.store.has_endpoint_grant(roles, key),
        )

    def _object_grants(self, roles: Sequence[str], key: str):
        return cached(
            ("object", tuple(roles), key),
            lambda: self.store.object_grants(roles, key),
        )

    def _field_grants(self, roles: Sequence[str], key: str):
        return cached(
            ("field", tuple(roles), key),
            lambda: self.store.field_grants(roles, key),
        )

    # -- endpoint tier ----------------------------------------------------

    def check_endpoint(self, user, key: str) -> bool:
        self._spec(key)
        if self._is_superuser(user):
            return True
        roles = self.roles_for(user)
        return bool(roles) and self._endpoint_grant(roles, key)

    def require(self, user, key: str) -> None:
        if not self.check_endpoint(user, key):
            raise PermissionDenied(key)

    # -- object tier ------------------------------------------------------

    def scope(self, user, key: str) -> ScopeResult:
        spec = self._spec(key)
        if self._is_superuser(user):
            return ScopeResult(ScopeKind.ALL)

        roles = self.roles_for(user)

        # Being unable to reach the endpoint at all implies there are no rows
        # to perform it on.  Without this, a role holding an object grant but
        # no endpoint grant would pass ``require_object`` — a caller reaching
        # the service layer directly (task, command, admin) would never have
        # had the endpoint tier checked for them.
        if not roles or not self._endpoint_grant(roles, key):
            return ScopeResult(ScopeKind.DENY)

        grants = self._object_grants(roles, key)
        if not grants:
            return ScopeResult(ScopeKind.DENY)

        ctx = self._context_builder(user)
        combined: Q | None = None
        needs_distinct = False

        for grant in grants:
            if not grant.conditions:
                # An unconditional grant means "everything".  It must
                # short-circuit rather than be OR-ed in: Django collapses
                # ``Q() | Q(x=1)`` to ``Q(x=1)``, which would *narrow* the
                # result instead of widening it.
                return ScopeResult(ScopeKind.ALL)

            conjunction: Q | None = None
            for condition_id, raw_params in grant.conditions:
                condition_spec = self.registry.condition(condition_id)
                # A filter declared for one object must not be composed onto
                # another.  Left unchecked, a ``crate`` filter on a ``widget``
                # endpoint compiles happily and filters widgets on the wrong
                # column — no error, plausible rows, silently wrong.
                if (
                    condition_spec.object_key is not None
                    and condition_spec.object_key != spec.resource
                ):
                    raise ConfigurationError(
                        f"Filter {condition_id!r} is declared for object "
                        f"{condition_spec.object_key!r} but grant {grant.name!r} "
                        f"applies it to {key!r}, whose object is "
                        f"{spec.resource!r}."
                    )
                params = condition_spec.bind(raw_params)
                condition_q = condition_spec.cls().as_q(ctx, **params)
                conjunction = condition_q if conjunction is None else conjunction & condition_q
                needs_distinct = needs_distinct or condition_spec.multi_valued

            combined = conjunction if combined is None else combined | conjunction

        return ScopeResult(ScopeKind.FILTERED, combined, needs_distinct)

    def apply_scope(self, qs: QuerySet, *, user, key: str) -> QuerySet:
        result = self.scope(user, key)
        if result.kind is ScopeKind.DENY:
            return qs.none()
        if result.kind is ScopeKind.ALL:
            return qs
        filtered = qs.filter(result.q)
        return filtered.distinct() if result.distinct else filtered

    def check_object(self, user, key: str, obj) -> bool:
        result = self.scope(user, key)
        if result.kind is ScopeKind.DENY:
            return False
        if result.kind is ScopeKind.ALL:
            return True
        # Same ``Q`` as the list path, so a row can never be invisible in the
        # list yet reachable by id.
        manager = type(obj)._default_manager
        return manager.filter(result.q).filter(pk=obj.pk).exists()

    def require_object(self, user, key: str, obj) -> None:
        if not self.check_object(user, key, obj):
            raise PermissionDenied(key)

    # -- field tier -------------------------------------------------------

    def granted_fields(self, user, key: str) -> frozenset[str]:
        roles = self.roles_for(user)
        grants = self._field_grants(roles, key) if roles else []
        out: set[str] = set()
        for grant in grants:
            out |= grant.allowed_fields  # union: grants only ever reveal more
        return frozenset(out)

    def visible_fields(self, user, key: str, candidates: Iterable[str]) -> set[str]:
        """Subset of ``candidates`` this user may see (or write) for ``key``.

        Only fields listed on the key's ``KeySpec.fields`` are controlled;
        everything else passes through.  Adopting the field tier therefore
        costs one declaration per *sensitive* field, not per column.
        """
        spec = self._spec(key)
        candidates = set(candidates)
        if self._is_superuser(user):
            return candidates
        controlled = candidates & set(spec.fields)
        if not controlled:
            return candidates
        return (candidates - controlled) | (controlled & self.granted_fields(user, key))

    def strip_fields(self, data: Mapping[str, Any], *, user, key: str) -> dict:
        allowed = self.visible_fields(user, key, data.keys())
        return {k: v for k, v in data.items() if k in allowed}

    def allowed_values(self, user, key: str) -> dict[str, frozenset]:
        """Permitted values per field, unioned across the grants that constrain it.

        A grant that permits the field but declares no constraint contributes
        nothing here — it does not lift another grant's constraint.  Treating
        silence as "any value" would let a permissive grant quietly widen a
        restriction, which is the opposite of fail-closed.
        """
        roles = self.roles_for(user)
        grants = self._field_grants(roles, key) if roles else []
        out: dict[str, set] = {}
        for grant in grants:
            for field_name, values in grant.allowed_values.items():
                out.setdefault(field_name, set()).update(values)
        return {f: frozenset(v) for f, v in out.items()}

    def _assert_reference_in_scope(self, user, governing_key: str, value) -> None:
        """The referenced row must be one this user could read under ``key``."""
        spec = self._spec(governing_key)
        if spec.model is None:
            raise ConfigurationError(
                f"Key {governing_key!r} governs a reference but declares no model."
            )

        values = value if isinstance(value, (list, tuple, set)) else [value]
        for item in values:
            if item is None:
                continue
            if isinstance(item, spec.model):
                permitted = self.check_object(user, governing_key, item)
            else:
                result = self.scope(user, governing_key)
                manager = spec.model._default_manager
                if result.kind is ScopeKind.DENY:
                    permitted = False
                elif result.kind is ScopeKind.ALL:
                    permitted = manager.filter(pk=item).exists()
                else:
                    permitted = manager.filter(result.q).filter(pk=item).exists()
            if not permitted:
                raise PermissionDenied(
                    governing_key,
                    f"Referenced {spec.resource} {item!r} is outside your scope.",
                )

    def assert_writable(self, data: Mapping[str, Any], *, user, key: str) -> None:
        """Full write-side check: which fields, which values, which references.

        Deliberately one call rather than three.  A separate "now also check
        the values" step is a step someone forgets, and the forgetting is
        silent — the picker is filtered client-side, so nothing looks wrong
        until a caller posts an id it was never offered.
        """
        spec = self._spec(key)

        # 0. may they reach this endpoint at all
        #
        # Defence in depth rather than the primary gate — that is the caller's
        # ``@requires``.  But this check only judges *controlled* fields, so a
        # payload of ordinary ones would otherwise pass completely, and a
        # service that forgot its decorator would be wide open.
        if not self.check_endpoint(user, key):
            raise PermissionDenied(key)

        # 1. which fields may be written at all
        allowed = self.visible_fields(user, key, data.keys())
        denied = sorted(set(data) - allowed)
        if denied:
            raise PermissionDenied(
                key, f"Not permitted to write field(s): {', '.join(denied)}."
            )

        if self._is_superuser(user):
            return

        # 2. which values those fields may take
        for field_name, permitted in self.allowed_values(user, key).items():
            if field_name in data and data[field_name] not in permitted:
                raise PermissionDenied(
                    key,
                    f"Not permitted to set {field_name}={data[field_name]!r}; "
                    f"allowed: {sorted(permitted)}.",
                )

        # 3. which rows a foreign key may point at
        for field_name, governing_key in spec.fk_scopes.items():
            if field_name in data:
                self._assert_reference_in_scope(
                    user, governing_key, data[field_name]
                )

    # -- diagnostics ------------------------------------------------------

    def explain(self, user, key: str, obj=None) -> Trace:
        """Human-readable account of a decision.

        Once rules live in data, "why can't this user do X?" stops being
        answerable by reading code — so the trace is part of the contract,
        not a debugging afterthought.
        """
        trace = Trace(key=key, roles=self.roles_for(user))

        if self._is_superuser(user):
            trace.add("superuser bypass enabled")
            trace.allowed = True
            return trace

        if not trace.roles:
            trace.add("principal has no roles → deny")
            trace.allowed = False
            return trace

        endpoint_ok = self._endpoint_grant(trace.roles, key)
        trace.add(
            f"endpoint grant: {'found' if endpoint_ok else 'MISSING'} for {key}"
        )

        spec = self._spec(key)
        grants = self._object_grants(trace.roles, key)
        if not grants:
            trace.add("object grants: none → deny all rows")
        for grant in grants:
            if not grant.conditions:
                trace.add(f"object grant {grant.name!r}: unconditional → all rows")
            else:
                conds = " AND ".join(b for b, _ in grant.conditions)
                trace.add(f"object grant {grant.name!r}: {conds}")
        in_scope = None
        if obj is not None:
            in_scope = self.check_object(user, key, obj)
            trace.add(
                f"object {obj!r}: {'in scope' if in_scope else 'OUT of scope'}"
            )

        granted = self.granted_fields(user, key)
        if spec.fields:
            hidden = sorted(set(spec.fields) - granted)
            trace.add(
                f"controlled fields: granted={sorted(granted) or '∅'} "
                f"withheld={hidden or '∅'}"
            )

        # When a row was named, the question asked was "may they do this to
        # *this row*", and the object tier is half that answer. Reporting the
        # endpoint verdict alone would tell an administrator "allowed" about a
        # row the same policy refuses.
        trace.allowed = endpoint_ok if in_scope is None else (endpoint_ok and in_scope)
        return trace
