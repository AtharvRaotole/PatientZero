"""MCP client used by the agent. Transport: the official `mcp` SDK in-memory transport (default) or stdio.

Each call opens a short-lived client session against the shared FastMCP server object (about 3 ms over the
in-memory transport), so concurrent HTTP requests never share an event loop or a session. Every call is timed.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

BACKEND_DIR = Path(__file__).resolve().parents[2]
TRANSPORT = os.getenv("MCP_AGENT_TRANSPORT", "memory").lower()  # memory | stdio
TOOLS_TTL = float(os.getenv("MCP_AGENT_TOOLS_TTL", "300"))  # seconds the tools/list result is reused


class McpCallError(RuntimeError):
    """The MCP server returned isError for a tool call (the tool raised)."""


@dataclass
class CallResult:
    tool: str
    args: dict
    body: Any
    ms: float
    transport: str
    tools_listed: int = 0
    list_ms: float = 0.0
    meta: dict = field(default_factory=dict)


_tls = threading.local()


def _server():
    from ..mcp_server import mcp
    return mcp


def _run(coro_fn, *a):
    """Run an async MCP exchange from sync code (FastAPI runs sync endpoints in worker threads)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # One event loop per worker thread, reused across calls (asyncio.run would build and tear down a loop
        # and its executor on every request). The task copies the caller's context, so the tenant carries over.
        loop = getattr(_tls, "loop", None)
        if loop is None or loop.is_closed():
            loop = _tls.loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro_fn(*a))
    ctx = contextvars.copy_context()  # keep request context (tenant) in the helper thread
    with concurrent.futures.ThreadPoolExecutor(1) as ex:  # called from inside a running loop: use a thread
        return ex.submit(ctx.run, asyncio.run, coro_fn(*a)).result()


class _Session:
    def __init__(self, transport: str):
        self.transport = transport

    async def __aenter__(self):
        if self.transport == "stdio":
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                           cwd=str(BACKEND_DIR), env=dict(os.environ))
            self._cm = stdio_client(params)
            r, w = await self._cm.__aenter__()
            self._sess = ClientSession(r, w)
            s = await self._sess.__aenter__()
            await s.initialize()
            return s
        from mcp.shared.memory import create_connected_server_and_client_session
        self._cm = create_connected_server_and_client_session(_server()._mcp_server)
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc):
        if self.transport == "stdio":
            await self._sess.__aexit__(*exc)
        return await self._cm.__aexit__(*exc)


_tools_cache: dict[str, tuple[float, list[dict]]] = {}
_tools_lock = threading.Lock()


def _tool_dicts(listing) -> list[dict]:
    return [{"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
            for t in listing.tools]


def _parse(res) -> Any:
    text = "".join(getattr(c, "text", "") for c in res.content)
    if res.isError:
        raise McpCallError(text or "MCP tool error")
    if res.structuredContent is not None:
        sc = res.structuredContent
        val = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        if not (isinstance(val, str) and val[:1] in "{["):
            return val
        text = val  # a tool that returns pre-serialised JSON text
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text


async def _list(transport: str) -> tuple[list[dict], float]:
    async with _Session(transport) as s:
        t = time.perf_counter()
        listing = await s.list_tools()
        return _tool_dicts(listing), round((time.perf_counter() - t) * 1000, 2)


def list_tools(transport: str | None = None, refresh: bool = False) -> tuple[list[dict], float, bool]:
    """tools/list over MCP. Returns (tools, ms, cached). The listing is reused for TOOLS_TTL seconds."""
    tr = transport or TRANSPORT
    with _tools_lock:
        hit = _tools_cache.get(tr)
    if hit and not refresh and time.time() - hit[0] < TOOLS_TTL:
        return hit[1], 0.0, True
    tools, ms = _run(_list, tr)
    with _tools_lock:
        _tools_cache[tr] = (time.time(), tools)
    return tools, ms, False


async def _call(transport: str, name: str, args: dict, want_list: bool):
    async with _Session(transport) as s:
        listed, list_ms = None, 0.0
        if want_list:
            t = time.perf_counter()
            listed = _tool_dicts(await s.list_tools())
            list_ms = round((time.perf_counter() - t) * 1000, 2)
        t = time.perf_counter()
        res = await s.call_tool(name, args)
        ms = round((time.perf_counter() - t) * 1000, 2)
        return res, ms, listed, list_ms


_CHAIN: contextvars.ContextVar["ChainSession | None"] = contextvars.ContextVar("pz_mcp_session", default=None)


class ChainSession:
    """One MCP client session shared by every tools/call of one request (the router's tool chain).

    A session costs an initialize handshake plus a task group; opening one per call made a 5-call chain cost about
    14 ms of CPU. The session is owned by one task on this worker thread's event loop and fed through a queue, so
    each sync call only runs the loop until its own result is back. The session lives for one request, so its
    server side sees that request's tenant. `server_ctx` is the context the server side runs in (the caller
    passes one with the agent chain switched off, so server-side tools never re-enter MCP)."""

    def __init__(self, transport: str | None = None, server_ctx: contextvars.Context | None = None):
        self.transport = transport or TRANSPORT
        self.server_ctx = server_ctx
        self.loop = None
        self.calls = 0

    def __enter__(self):
        try:
            asyncio.get_running_loop()
            return None  # inside a running loop (async caller): per-call sessions instead
        except RuntimeError:
            pass
        loop = getattr(_tls, "loop", None)
        if loop is None or loop.is_closed():
            loop = _tls.loop = asyncio.new_event_loop()
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ready = loop.create_future()
        ctx = self.server_ctx or contextvars.copy_context()
        self.task = loop.create_task(self._owner(), context=ctx)
        loop.run_until_complete(asyncio.wait({self.ready, self.task}, return_when=asyncio.FIRST_COMPLETED))
        if self.task.done():
            self.task.result()  # raises the session error
        self._token = _CHAIN.set(self)
        return self

    async def _owner(self):
        async with _Session(self.transport) as s:
            self.ready.set_result(True)
            while True:
                item = await self.queue.get()
                if item is None:
                    return
                fn, fut = item
                try:
                    fut.set_result(await fn(s))
                except BaseException as e:  # noqa: BLE001 - handed back to the caller
                    if not fut.done():
                        fut.set_exception(e)

    def run(self, fn):
        if self.task.done():
            raise ConnectionError("MCP chain session closed")
        fut = self.loop.create_future()
        self.queue.put_nowait((fn, fut))
        self.calls += 1
        return self.loop.run_until_complete(fut)

    def __exit__(self, *exc):
        if self.loop is None:
            return False
        _CHAIN.reset(self._token)
        if not self.task.done():
            self.queue.put_nowait(None)
            try:
                self.loop.run_until_complete(asyncio.wait_for(self.task, 5))
            except Exception:  # noqa: BLE001 - never mask the request's own outcome
                self.task.cancel()
        return False


async def _call_in(s, name: str, args: dict, want_list: bool):
    listed, list_ms = None, 0.0
    if want_list:
        t = time.perf_counter()
        listed = _tool_dicts(await s.list_tools())
        list_ms = round((time.perf_counter() - t) * 1000, 2)
    t = time.perf_counter()
    res = await s.call_tool(name, args)
    return res, round((time.perf_counter() - t) * 1000, 2), listed, list_ms


def call_tool(name: str, args: dict, transport: str | None = None) -> CallResult:
    """tools/call over MCP (listing tools in the same session when the cached listing is stale). Inside a
    ChainSession the request's shared session is used; otherwise a short session is opened for this call."""
    tr = transport or TRANSPORT
    chain = _CHAIN.get()
    if chain is not None and chain.transport == tr:
        return _call_chained(chain, name, args, tr)
    with _tools_lock:
        hit = _tools_cache.get(tr)
    stale = not hit or time.time() - hit[0] >= TOOLS_TTL
    t0 = time.perf_counter()
    res, ms, listed, list_ms = _run(_call, tr, name, args, stale)
    if listed is not None:
        with _tools_lock:
            _tools_cache[tr] = (time.time(), listed)
        tools = listed
    else:
        tools = hit[1]
    if name not in {t["name"] for t in tools}:
        raise McpCallError(f"tool '{name}' is not in the MCP server's tools/list")
    body = _parse(res)
    return CallResult(tool=name, args=args, body=body, ms=ms, transport=tr, tools_listed=len(tools),
                      list_ms=list_ms, meta={"session_ms": round((time.perf_counter() - t0) * 1000, 2)})


def _call_chained(chain: ChainSession, name: str, args: dict, tr: str) -> CallResult:
    with _tools_lock:
        hit = _tools_cache.get(tr)
    stale = not hit or time.time() - hit[0] >= TOOLS_TTL
    t0 = time.perf_counter()
    res, ms, listed, list_ms = chain.run(lambda s: _call_in(s, name, args, stale))
    if listed is not None:
        with _tools_lock:
            _tools_cache[tr] = (time.time(), listed)
        tools = listed
    else:
        tools = hit[1]
    if name not in {t["name"] for t in tools}:
        raise McpCallError(f"tool '{name}' is not in the MCP server's tools/list")
    body = _parse(res)
    return CallResult(tool=name, args=args, body=body, ms=ms, transport=tr, tools_listed=len(tools),
                      list_ms=list_ms, meta={"session_ms": round((time.perf_counter() - t0) * 1000, 2),
                                             "shared_session": True, "call_no": chain.calls})
