"""Tenants: one FalkorDB graph per tenant (multigraph). Memory never crosses a tenant boundary.

Each tenant's fleet, incidents, playbooks, sessions and crew runs live in its own graph (fleet:acme,
fleet:globex). The current tenant is a context variable, set per request by TenantMiddleware (query string
`?tenant=` or `X-Tenant` header) or explicitly with `use()` (JSON bodies). db.graph() reads it, so every
existing query and tool is tenant-scoped without being rewritten. Tenant names are validated against an
allowlist: an unknown tenant is a 400, never a fallback to another tenant's data.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
from typing import Iterator
from urllib.parse import parse_qs

from . import config

DEFAULT = "acme"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


def _registry() -> dict[str, str]:
    """tenant -> graph. TENANTS env: 'acme,globex' (graph fleet:<name>) or 'acme=fleet:acme,globex=fleet:globex'.
    FALKORDB_GRAPH, when set, overrides the default tenant's graph (backwards compatible)."""
    out: dict[str, str] = {}
    for part in (os.getenv("TENANTS") or "acme,globex").split(","):
        part = part.strip().lower()
        if not part:
            continue
        name, _, graph = part.partition("=")
        if NAME_RE.match(name):
            out[name] = graph or f"fleet:{name}"
    out.setdefault(DEFAULT, "fleet:acme")
    if os.getenv("FALKORDB_GRAPH"):
        out[DEFAULT] = config.GRAPH_NAME
    return out


TENANTS: dict[str, str] = _registry()


def _display() -> dict[str, str]:
    names = {}
    for t in TENANTS:
        try:
            names[t] = json.loads((config.ROOT / "data" / "fleet" / f"{t}.json").read_text()).get("company") or t
        except (OSError, ValueError):
            names[t] = t.capitalize()
    return names


COMPANY = _display()
_current: contextvars.ContextVar[str] = contextvars.ContextVar("tenant", default=DEFAULT)
# True once a request (middleware) or an explicit use() pinned the tenant: tools called inside that context may
# not switch to another tenant (so an LLM choosing tool arguments cannot read across tenants).
_pinned: contextvars.ContextVar[bool] = contextvars.ContextVar("tenant_pinned", default=False)


class UnknownTenant(ValueError):
    pass


def validate(tenant: str | None) -> str:
    """Normalise and check a tenant name against the allowlist. None/'' means the default tenant."""
    t = (tenant or DEFAULT).strip().lower()
    if not NAME_RE.match(t) or t not in TENANTS:
        raise UnknownTenant(f"unknown tenant {t[:40]!r}; allowed: {', '.join(sorted(TENANTS))}")
    return t


def current() -> str:
    return _current.get()


def graph_name(tenant: str | None = None) -> str:
    return TENANTS[validate(tenant) if tenant else current()]


def company(tenant: str | None = None) -> str:
    t = validate(tenant) if tenant else current()
    return COMPANY.get(t, t)


@contextlib.contextmanager
def use(tenant: str | None) -> Iterator[str]:
    t = validate(tenant)
    token, ptoken = _current.set(t), _pinned.set(True)
    try:
        yield t
    finally:
        _current.reset(token)
        _pinned.reset(ptoken)


def for_tool(tenant: str | None) -> str:
    """Tenant for a tool call (MCP). Inside a pinned request context only that tenant is allowed."""
    if not tenant:
        return current()
    t = validate(tenant)
    if _pinned.get() and t != current():
        raise UnknownTenant(f"cross-tenant access refused: this request is pinned to tenant {current()!r}")
    return t


def set_current(tenant: str | None) -> str:
    """Set the tenant for the rest of the current context (an asyncio task or a worker thread)."""
    t = validate(tenant)
    _current.set(t)
    return t


def listing() -> list[dict]:
    return [{"id": t, "company": COMPANY.get(t, t), "graph": g, "default": t == DEFAULT} for t, g in TENANTS.items()]


class TenantMiddleware:
    """Pure ASGI middleware: picks the tenant from `?tenant=` or the X-Tenant header for the whole request.
    Endpoints with a JSON body may override it with an explicit `tenant` field via use()."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = None
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if qs.get("tenant"):
            raw = qs["tenant"][0]
        else:
            for k, v in scope.get("headers") or []:
                if k == b"x-tenant":
                    raw = v.decode("latin-1")
                    break
        try:
            t = validate(raw)
        except UnknownTenant as e:
            body = json.dumps({"error": "bad_request", "message": str(e)}).encode()
            await send({"type": "http.response.start", "status": 400,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        token, ptoken = _current.set(t), _pinned.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
            _pinned.reset(ptoken)
