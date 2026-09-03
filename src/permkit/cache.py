"""Remember grant lookups for the length of one unit of work.

Every tier asks the store the same question — *what may these roles do with
this key?* — and asks it again for each row a field-stripping loop touches. The
answers cannot change while one request is being served, so asking once is
free correctness rather than a trade.

There is no request id anywhere in this. A :class:`~contextvars.ContextVar`
holds a plain dict; :func:`grant_cache` swaps a fresh one in on the way into a
unit of work and puts the previous one back on the way out. The "request" is
literally the span between those two moments — which is why the same mechanism
works unchanged for a Celery task or a management command, neither of which
has a request to take an id from.

**Nothing is cached outside a scope.** A ContextVar's default would otherwise
be a process-global dict that nothing ever clears, and stale grants are a
security bug, not a slow page. So forgetting the middleware costs queries and
can never cost correctness — which is the right way round for this to fail.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

#: None means "no scope is open" — do not cache. Threads in a pool are reused
#: between requests, so the reset in ``grant_cache`` is what bounds this, not
#: the ContextVar's natural lifetime.
_SCOPE: ContextVar[dict | None] = ContextVar("permkit_grant_cache", default=None)


@contextmanager
def grant_cache():
    """Open a scope in which grant lookups are resolved once.

    Use it around a unit of work that must see one consistent set of grants::

        with grant_cache():
            for widget in queryset:
                require_object(actor, "widget.update", widget)

    Nesting is safe: the inner scope gets its own dict and the outer one is
    restored afterwards, so a nested unit of work cannot poison its caller.
    """
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def cached(key: tuple, produce: Callable[[], Any]) -> Any:
    """Return ``produce()``, remembering it if a scope is open."""
    store = _SCOPE.get()
    if store is None:
        return produce()
    if key not in store:
        store[key] = produce()
    return store[key]


def is_active() -> bool:
    """True when a scope is open. For tests and diagnostics."""
    return _SCOPE.get() is not None


class GrantCacheMiddleware:
    """Wrap each request in a grant-cache scope.

    Add it anywhere after authentication::

        MIDDLEWARE = [..., "permkit.cache.GrantCacheMiddleware"]

    A permission edited in the admin is visible to the *next* request, which is
    the same freshness the system had before — the scope closes when the
    response is returned.

    One caveat worth knowing: a streaming response produces its body after the
    middleware has returned, so the rows it yields are resolved outside the
    scope and simply go uncached. Correct, just not accelerated.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with grant_cache():
            return self.get_response(request)
