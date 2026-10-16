"""FalkorDB access + the parameterised Cypher catalogue + the tool trace."""
from __future__ import annotations

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


def client() -> FalkorDB:
    global _db
    if _db is None and config.FALKORDB_URL:
        _db = FalkorDB.from_url(config.FALKORDB_URL)
    if _db is None:
        kw: dict[str, Any] = dict(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT,
                                  socket_timeout=5, socket_connect_timeout=2)
        if config.FALKORDB_USERNAME:
            kw["username"] = config.FALKORDB_USERNAME
        if config.FALKORDB_PASSWORD:
            kw["password"] = config.FALKORDB_PASSWORD
        if config.FALKORDB_SSL:
            kw["ssl"] = True
        _db = FalkorDB(**kw)
    return _db


def graph():
    return client().select_graph(config.GRAPH_NAME)


@dataclass
class Trace:
    steps: list[dict] = field(default_factory=list)

    def run(self, tool: str, query: str, params: dict | None = None, *, write: bool = False,
            note: str | None = None) -> list[dict]:
        """Run the named query from queries/*.cypher. Reads use ro_query, writes use query."""
        if query not in CATALOGUE:
            raise KeyError(f"query '{query}' not found in queries/*.cypher")
        cypher = CATALOGUE[query]["cypher"]
        params = params or {}
        t0 = time.perf_counter()
        try:
            g = graph()
            res = with_busy_retry(lambda: g.query(cypher, params) if write else g.ro_query(cypher, params))
        except Exception as e:  # noqa: BLE001
            ms = round((time.perf_counter() - t0) * 1000, 2)
            self.steps.append(dict(tool=tool, query=query, cypher=cypher, params=_short(params), ms=ms,
                                   rows=0, error=str(e), file=CATALOGUE[query]["file"]))
            if _is_conn_error(e):
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


def _is_conn_error(e: Exception) -> bool:
    name = type(e).__name__.lower()
    msg = str(e).lower()
    return ("connection" in name or "timeout" in name or "connection" in msg or "refused" in msg
            or "timed out" in msg)


def ping() -> dict:
    try:
        c = client()
        c.connection.ping()
        graphs = c.list_graphs()
        return {"ok": True, "graphs": graphs, "graph": config.GRAPH_NAME,
                "graph_loaded": config.GRAPH_NAME in graphs}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "graph": config.GRAPH_NAME}
