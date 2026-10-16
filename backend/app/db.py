"""FalkorDB access + the parameterised Cypher catalogue + the tool trace."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from falkordb import FalkorDB

from . import config

QUERIES_DIR = config.ROOT / "queries"


class GraphUnavailable(RuntimeError):
    """FalkorDB cannot be reached. The product refuses to answer from anywhere else."""


def _load_catalogue(directory: Path = QUERIES_DIR) -> dict[str, dict]:
    """Parse queries/*.cypher (the single Cypher source, shared with ingest/verify.py).

    Each named block starts with `// @query <name>`; other `//` lines are comments.
    Returns {query_name: {"cypher": str, "file": "queries/<tool>.cypher"}}.
    """
    cat: dict[str, dict] = {}
    for f in sorted(directory.glob("*.cypher")):
        name, buf = None, []
        for line in f.read_text().splitlines() + ["// @query __end__"]:
            m = re.match(r"\s*//\s*@query\s+(\S+)", line)
            if m:
                if name and "".join(buf).strip():
                    cat[name] = {"cypher": "\n".join(buf).strip(), "file": f"queries/{f.name}"}
                name, buf = m.group(1), []
            elif name and not line.strip().startswith("//"):
                buf.append(line)
    return cat


CATALOGUE = _load_catalogue()


# FalkorDB rejects a query outright when its queue is full ("Max pending queries exceeded", MAX_QUEUED_QUERIES).
# The query never ran, so retrying after a short jittered backoff is safe for reads and writes alike.
BUSY_RETRIES = int(__import__("os").getenv("FALKORDB_BUSY_RETRIES", "5"))


def is_busy(e: Exception) -> bool:
    return "max pending queries" in str(e).lower()


def with_busy_retry(fn):
    import random
    for i in range(BUSY_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if not is_busy(e) or i == BUSY_RETRIES:
                raise
            time.sleep(min(0.5, 0.02 * 2 ** i) * (0.5 + random.random()))


_db: FalkorDB | None = None


POOL_MAX = int(os.getenv("FALKORDB_POOL_MAX", "64"))      # connections per process (FalkorDB queues the rest)
POOL_WAIT_S = float(os.getenv("FALKORDB_POOL_WAIT_S", "5"))  # wait for a free connection, then GraphUnavailable


def client() -> FalkorDB:
    """One bounded, blocking connection pool per process: a burst never opens unbounded sockets, a caller waits
    up to POOL_WAIT_S for a free connection, and every socket has connect and read timeouts."""
    global _db
    if _db is not None:
        return _db
    import redis
    common = dict(decode_responses=True, max_connections=POOL_MAX, timeout=POOL_WAIT_S,
                  socket_timeout=5, socket_connect_timeout=2)
    if config.FALKORDB_URL:
        url = config.FALKORDB_URL
        if url.startswith("falkor://"):
            url = "redis://" + url[len("falkor://"):]
        elif url.startswith("falkors://"):
            url = "rediss://" + url[len("falkors://"):]
        pool = redis.BlockingConnectionPool.from_url(url, **common)
    else:
        kw: dict[str, Any] = dict(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, **common)
        if config.FALKORDB_USERNAME:
            kw["username"] = config.FALKORDB_USERNAME
        if config.FALKORDB_PASSWORD:
            kw["password"] = config.FALKORDB_PASSWORD
        if config.FALKORDB_SSL:
            kw["connection_class"] = redis.SSLConnection
        pool = redis.BlockingConnectionPool(**kw)
    _db = FalkorDB(connection_pool=pool)
    return _db


def graph(name: str | None = None):
    """The current tenant's graph (see tenants.py), or an explicit graph name."""
    from . import tenants
    return client().select_graph(name or tenants.graph_name())


@dataclass
class Trace:
    steps: list[dict] = field(default_factory=list)
    graph_name: str | None = None  # None: the current tenant's graph at query time

    def run(self, tool: str, query: str, params: dict | None = None, *, write: bool = False,
            note: str | None = None) -> list[dict]:
        """Run the named query from queries/*.cypher. Reads use ro_query, writes use query."""
        if query not in CATALOGUE:
            raise KeyError(f"query '{query}' not found in queries/*.cypher")
        cypher = CATALOGUE[query]["cypher"]
        params = params or {}
        t0 = time.perf_counter()
        try:
            g = graph(self.graph_name)
            res = with_busy_retry(lambda: g.query(cypher, params) if write else g.ro_query(cypher, params))
        except Exception as e:  # noqa: BLE001
            ms = round((time.perf_counter() - t0) * 1000, 2)
            down = _is_conn_error(e)
            self.steps.append(dict(tool=tool, query=query, cypher=cypher, params=_short(params), ms=ms,
                                   rows=0, error=DB_DOWN if down else f"query failed ({type(e).__name__})",
                                   file=CATALOGUE[query]["file"]))
            if down:
                raise GraphUnavailable(f"graph unavailable: {e}") from e
            raise
        ms = round((time.perf_counter() - t0) * 1000, 2)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
        rows = [dict(zip(header, r)) for r in res.result_set]
        step = dict(tool=tool, query=query, cypher=cypher, params=_short(params), ms=ms, rows=len(rows),
                    write=write)
        if note:
            step["note"] = note
        step["file"] = CATALOGUE[query]["file"]
        self.steps.append(step)
        return rows

    def compact(self, tool: str, query: str | None = None):
        """Merge consecutive steps of the same tool/query (e.g. resolve_entity per token) into one trace row."""
        merged: list[dict] = []
        for st in self.steps:
            prev = merged[-1] if merged else None
            if (prev and st["tool"] == tool == prev["tool"] and st.get("query") == prev.get("query")
                    and (query is None or st.get("query") == query) and not st.get("error") and not prev.get("error")):
                prev["ms"] = round(prev["ms"] + st["ms"], 2)
                prev["rows"] += st["rows"]
                calls = prev.setdefault("calls", [prev["params"]])
                calls.append(st["params"])
                prev["params"] = {"calls": calls}
                prev["note"] = f"{len(calls)} calls"
            else:
                merged.append(st)
        for m in merged:
            m.pop("calls", None)
        self.steps = merged

    def add(self, tool: str, note: str, **kw):
        self.steps.append(dict(tool=tool, query=None, cypher=None, params=kw.pop("params", {}),
                               ms=kw.pop("ms", 0), rows=kw.pop("rows", 0), note=note, **kw))


def _short(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, list) and len(v) > 25:
            out[k] = v[:25] + [f"... (+{len(v) - 25} more)"]
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "..."
        else:
            out[k] = v
    return out


DB_DOWN = "database unavailable"
_DOWN_WORDS = ("connection", "refused", "timed out", "timeout", "noauth", "wrongpass", "authentication",
               "invalid username-password", "user is disabled", "invalid password", "no route to host",
               "name or service not known", "temporary failure in name resolution", "broken pipe", "reset by peer",
               "loading the dataset in memory", "max number of clients")


def _is_conn_error(e: Exception) -> bool:
    """FalkorDB cannot serve us at all: unreachable, timing out, rejecting our credentials (a rotated password),
    still loading, or out of client slots. Every such error becomes GraphUnavailable (a clean 503)."""
    seen = 0
    while e is not None and seen < 5:
        name = type(e).__name__.lower()
        msg = str(e).lower()
        if ("connection" in name or "timeout" in name or "authentication" in name or "busyloading" in name
                or any(w in msg for w in _DOWN_WORDS)):
            return True
        e, seen = (e.__cause__ or e.__context__), seen + 1
    return False


def is_db_unavailable(e: BaseException) -> bool:
    return isinstance(e, GraphUnavailable) or (isinstance(e, Exception) and _is_conn_error(e))


def public_error(e: BaseException) -> dict:
    """What a client may see about a failure: a stable code and a fixed sentence, never the exception text."""
    if is_db_unavailable(e):
        return {"error": "database_unavailable", "message": "The database is unavailable. Try again shortly."}
    return {"error": "internal_error", "message": "Internal error. The details are in the server log."}


def ping() -> dict:
    from . import tenants
    name = tenants.graph_name()
    try:
        c = client()
        c.connection.ping()
        graphs = c.list_graphs()
        return {"ok": True, "graphs": graphs, "graph": name, "graph_loaded": name in graphs,
                "tenants_loaded": {t: g in graphs for t, g in tenants.TENANTS.items()}}
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("patientzero").warning("FalkorDB ping failed: %s", e)
        return {"ok": False, "error": DB_DOWN if _is_conn_error(e) else f"ping failed ({type(e).__name__})",
                "graph": name}
