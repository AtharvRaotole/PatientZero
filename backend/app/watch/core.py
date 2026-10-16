"""The watcher: OSV index head -> new MAL ids -> records -> graph (every tenant) -> exposure -> WatchEvents.

Honesty rules: only real OSV records are ingested; every check is recorded, including "not hit"; a replay of a past
advisory is stored with replay=true and labelled as a replay everywhere. Bounded work per poll: at most
WATCH_MAX_BATCH advisories, per-request timeouts, byte caps, and an overall deadline.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from .. import config, db
from ..db import CATALOGUE, GraphUnavailable
from ..semver_util import check
from . import osv

log = logging.getLogger("patientzero.watch")

INTERVAL_S = float(os.getenv("WATCH_INTERVAL_S", "600"))      # background poll period; 0 disables the loop
START_DELAY_S = float(os.getenv("WATCH_START_DELAY_S", "45"))
MAX_BATCH = int(os.getenv("WATCH_MAX_BATCH", "200"))           # advisories ingested per poll at most
BACKFILL = int(os.getenv("WATCH_BACKFILL", "25"))              # first poll on an empty watermark: newest N
DEPTH = int(os.getenv("WATCH_DEPTH", "6"))                     # exposure depth (max sensitivity)
DEADLINE_S = float(os.getenv("WATCH_DEADLINE_S", "90"))
KEEP_EVENTS = int(os.getenv("WATCH_KEEP_EVENTS", "1000"))      # per tenant graph
KEEP_POLLS = int(os.getenv("WATCH_KEEP_POLLS", "200"))
STATE_KEY = "patientzero:watch:state"
LOCK_KEY = "patientzero:watch:lock"

_poll_lock = threading.Lock()
_indexed: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ----------------------------------------------------------------------------- tenants and graph access
def tenant_graphs() -> dict[str, str]:
    """Every tenant fleet graph that exists (tenants.TENANTS when the tenants module is present)."""
    try:
        from .. import tenants
        reg = dict(tenants.TENANTS)
    except Exception:  # noqa: BLE001
        reg = {"acme": config.GRAPH_NAME}
    try:
        have = set(db.client().list_graphs())
    except Exception as e:  # noqa: BLE001
        raise GraphUnavailable(f"graph unavailable: {e}") from e
    return {t: g for t, g in reg.items() if g in have}


def current_tenant(explicit: str | None = None) -> str:
    graphs = tenant_graphs()
    if explicit:
        t = explicit.strip().lower()
        if t not in graphs:
            raise ValueError(f"unknown tenant {t[:40]!r}; allowed: {', '.join(sorted(graphs))}")
        return t
    try:
        from .. import tenants
        t = tenants.current()
        if t in graphs:
            return t
    except Exception:  # noqa: BLE001
        pass
    return next(iter(graphs), "acme")


class GraphRun:
    """Runs named queries from queries/*.cypher against one named graph and keeps a trace."""

    def __init__(self, graph_name: str):
        self.graph_name = graph_name
        self.steps: list[dict] = []

    def run(self, query: str, params: dict | None = None, write: bool = False) -> list[dict]:
        cypher = CATALOGUE[query]["cypher"]
        t0 = time.perf_counter()
        try:
            g = db.client().select_graph(self.graph_name)
            res = db.with_busy_retry(lambda: g.query(cypher, params or {}) if write else g.ro_query(cypher, params or {}))
        except Exception as e:  # noqa: BLE001
            if db._is_conn_error(e):
                raise GraphUnavailable(f"graph unavailable: {e}") from e
            raise
        ms = round((time.perf_counter() - t0) * 1000, 2)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
        rows = [dict(zip(header, r)) for r in res.result_set]
        self.steps.append(dict(tool="watch", query=query, graph=self.graph_name, ms=ms, rows=len(rows),
                               write=write, file=CATALOGUE[query]["file"]))
        return rows


def _ensure_indexes(gr: GraphRun) -> None:
    if gr.graph_name in _indexed:
        return
    for q in ("watch_index_event_ts", "watch_index_event_id", "watch_index_event_poll"):
        try:
            gr.run(q, write=True)
        except GraphUnavailable:
            raise
        except Exception:  # noqa: BLE001 - index already exists
            pass
    _indexed.add(gr.graph_name)


# ----------------------------------------------------------------------------- state (watermark, ETag) in FalkorDB
def _redis():
    return db.client().connection


def load_state(key: str = STATE_KEY) -> dict:
    try:
        raw = _redis().get(key)
        return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(key: str, st: dict) -> None:
    try:
        _redis().set(key, json.dumps(st))
    except Exception as e:  # noqa: BLE001
        log.warning("watch state not saved: %s", e)


def _acquire_lock(ttl: int = 300) -> str | None:
    token = uuid.uuid4().hex
    try:
        return token if _redis().set(LOCK_KEY, token, nx=True, ex=ttl) else None
    except Exception:  # noqa: BLE001 - redis commands unavailable: fall back to the process lock only
        return token


def _release_lock(token: str) -> None:
    try:
        r = _redis()
        v = r.get(LOCK_KEY)
        if isinstance(v, bytes):
            v = v.decode()
        if v == token:
            r.delete(LOCK_KEY)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------------- live broadcast (SSE)
class Broadcaster:
    def __init__(self, maxlen: int = 300):
        self.events: deque[tuple[int, dict]] = deque(maxlen=maxlen)
        self.seq = 0
        self.lock = threading.Lock()

    def publish(self, kind: str, data: dict) -> None:
        with self.lock:
            self.seq += 1
            self.events.append((self.seq, {"kind": kind, **data}))

    def since(self, seq: int) -> list[tuple[int, dict]]:
        with self.lock:
            return [(s, e) for s, e in self.events if s > seq]


BUS = Broadcaster()
LAST: dict[str, Any] = {"poll": None, "running": False, "loop": {"enabled": False, "interval_s": INTERVAL_S}}


# ----------------------------------------------------------------------------- exposure check
def check_advisories(gr: GraphRun, advs: list[osv.Advisory], depth: int = DEPTH) -> dict[str, dict]:
    """One `exposure` traversal for all packages in the batch, then npm semver per advisory on the last edge."""
    if not advs:
        return {}
    t0 = time.perf_counter()
    pkgs = sorted({a.pkg for a in advs})
    rows = gr.run("exposure", {"pkgs": pkgs, "depth": max(1, min(6, depth))})
    by_pkg: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_pkg[r["pkg"]].append(r)
    out: dict[str, dict] = {}
    for a in advs:
        exposed: dict[str, dict] = {}
        reachable: set[str] = set()
        for r in by_pkg.get(a.pkg, []):
            reachable.add(r["service"])
            ok, reason = _admits(a, r.get("requirement"))
            if ok and r["service"] not in exposed:
                exposed[r["service"]] = {"service": r["service"], "tier": r.get("tier"), "hops": r.get("hops"),
                                         "via": r.get("via"), "requirement": r.get("requirement"), "reason": reason}
        if exposed:
            verdict = "hit"
            reason = f"{len(exposed)} service(s) can install a malicious {a.pkg} version"
        elif reachable:
            verdict = "reachable_not_admitted"
            reason = f"{len(reachable)} service(s) depend on {a.pkg}, but no declared range admits a malicious version"
        else:
            verdict = "not_hit"
            reason = f"no service depends on {a.pkg} within {depth} hops"
        out[a.id + "|" + a.pkg] = {"verdict": verdict, "exposed": list(exposed.values()), "reachable": sorted(reachable),
                                   "reason": reason}
    ms = round((time.perf_counter() - t0) * 1000, 2)
    for v in out.values():
        v["check_ms"] = ms
    return out


def _admits(a: osv.Advisory, requirement: str | None) -> tuple[bool, str]:
    if a.all_versions:
        return True, f"every published version of {a.pkg} is malicious"
    for bv in a.bad_versions:
        ok, reason = check(bv, requirement, a.pkg)
        if ok:
            return True, reason
    for rng in a.other_ranges:
        try:
            import nodesemver
            if requirement and nodesemver.intersects(rng, str(requirement), loose=True):
                return True, f"{a.pkg} {requirement} overlaps the malicious range {rng}"
        except Exception:  # noqa: BLE001
            continue
    return False, ""


# ----------------------------------------------------------------------------- write events
def _record(graphs: dict[str, str], advs: list[osv.Advisory], *, poll_id: str, replay: bool, ingest: bool,
            depth: int = DEPTH) -> tuple[list[dict], list[dict]]:
    """Ingest (optional), check and write WatchEvents in every tenant graph. Returns (events, steps)."""
    events, steps = [], []
    ts = now_iso()
    for tenant, gname in graphs.items():
        gr = GraphRun(gname)
        _ensure_indexes(gr)
        if ingest and advs:
            gr.run("watch_ingest_advisories", {"rows": [a.row(ts) for a in advs]}, write=True)
        fleet = (gr.run("watch_fleet_size") or [{"services": 0}])[0]["services"]
        verdicts = check_advisories(gr, advs, depth)
        evs, hits = [], []
        for a in advs:
            v = verdicts[a.id + "|" + a.pkg]
            eid = f"{poll_id}:{tenant}:{a.id}:{a.pkg}"
            ev = dict(id=eid, ts=ts, poll_id=poll_id, tenant=tenant, advisory=a.id, aliases=a.aliases,
                      package=a.pkg, bad_versions=a.bad_versions[:50], all_versions=a.all_versions,
                      verdict=v["verdict"], exposed_services=[x["service"] for x in v["exposed"]],
                      exposed_count=len(v["exposed"]), reachable_services=v["reachable"], fleet_size=fleet,
                      depth=depth, replay=replay, published=a.published, modified=a.modified,
                      summary=a.summary[:200], url=a.url, reason=v["reason"], check_ms=v["check_ms"])
            evs.append(ev)
            hits += [{"id": eid, "service": s} for s in ev["exposed_services"]]
            events.append({**ev, "paths": [{"service": x["service"], "via": x["via"], "requirement": x["requirement"],
                                            "reason": x["reason"]} for x in v["exposed"][:10]]})
        for i in range(0, len(evs), 100):
            gr.run("watch_write_events", {"events": evs[i:i + 100]}, write=True)
        if hits:
            gr.run("watch_link_hits", {"hits": hits}, write=True)
        gr.run("watch_prune_events", {"keep": KEEP_EVENTS}, write=True)
        steps += gr.steps
    return events, steps


def _write_poll(graphs: dict[str, str], poll: dict) -> None:
    flat = {k: (v if isinstance(v, (int, float, str)) or v is None else json.dumps(v)[:500])
            for k, v in poll.items() if k in ("id", "ts", "source", "checked", "new_advisories", "already_known",
                                               "index_entries", "hits", "ms", "errors", "note",
                                               "published_oldest", "published_newest")}
    flat.setdefault("published_oldest", None)
    flat.setdefault("published_newest", None)
    for g in graphs.values():
        gr = GraphRun(g)
        gr.run("watch_write_poll", {"poll": flat}, write=True)
        gr.run("watch_prune_polls", {"keep": KEEP_POLLS}, write=True)


# ----------------------------------------------------------------------------- poll
def poll(max_batch: int = MAX_BATCH, source: str = "manual", graphs: dict[str, str] | None = None,
         state_key: str = STATE_KEY) -> dict:
    """One bounded poll. Raises RuntimeError('busy') when another poll holds the lock."""
    if not _poll_lock.acquire(blocking=False):
        raise RuntimeError("busy")
    token = _acquire_lock()
    if token is None:
        _poll_lock.release()
        raise RuntimeError("busy")
    LAST["running"] = True
    t0 = time.perf_counter()
    poll_id = "P-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    summary: dict[str, Any] = {"id": poll_id, "ts": now_iso(), "source": source, "replay": False, "checked": 0,
                               "new_advisories": 0, "already_known": 0, "index_entries": 0, "hits": 0,
                               "errors": 0, "fetch_errors": {}, "tenants": [], "note": ""}
    try:
        max_batch = max(1, min(MAX_BATCH, int(max_batch)))
        graphs = graphs or tenant_graphs()
        summary["tenants"] = sorted(graphs)
        st = load_state(state_key)
        head = osv.fetch_index_head(etag=st.get("etag") if st.get("watermark") else None)
        summary["index_bytes"] = head.bytes
        if head.unchanged:
            summary["note"] = "OSV npm index unchanged since the last poll (HTTP 304)"
        else:
            summary["index_entries"] = len(head.entries)
            wm = st.get("watermark")
            fresh = [(ts, i) for ts, i in head.entries if not wm or ts > wm]
            if not wm:
                fresh = fresh[:BACKFILL]
            seen: set[str] = set()
            fresh = [(ts, i) for ts, i in sorted(fresh) if not (i in seen or seen.add(i))]  # oldest first, unique
            batch = fresh[:max_batch]
            ids = [i for _, i in batch]
            first = next(iter(graphs.values()), None)
            known = {r["id"] for r in GraphRun(first).run("watch_known_advisories", {"ids": ids})} if first and ids else set()
            todo = [i for i in ids if i not in known]
            summary["already_known"] = len(ids) - len(todo)
            recs, errs = osv.fetch_records(todo, deadline_s=DEADLINE_S)
            summary["fetch_errors"] = dict(list(errs.items())[:10])
            summary["errors"] = len(errs)
            advs = [a for i in todo if i in recs for a in osv.normalize(recs[i])]
            summary["new_advisories"] = len({a.id for a in advs})
            summary["checked"] = len(advs)
            events, _ = _record(graphs, advs, poll_id=poll_id, replay=False, ingest=True)
            summary["hits"] = sum(1 for e in events if e["verdict"] == "hit")
            for e in events:
                BUS.publish("event", e)
            # Advance the watermark only past what was processed; failed fetches are retried next poll.
            done = [ts for ts, i in batch if i not in errs]
            if errs:
                first_err = min(ts for ts, i in batch if i in errs)
                done = [ts for ts in done if ts < first_err]
            new_wm = max(done) if done else wm
            save_state(state_key, {"watermark": new_wm or wm, "etag": head.etag if len(fresh) <= max_batch and not errs else None,
                        "updated": now_iso()})
            summary["watermark"] = new_wm or wm
            summary["remaining"] = max(0, len(fresh) - len(batch))
            pubs = sorted(a.published for a in advs if a.published)
            summary["published_oldest"], summary["published_newest"] = (pubs[0], pubs[-1]) if pubs else (None, None)
            span = (f" (OSV published {pubs[0][:10]}" + (f" to {pubs[-1][:10]}" if pubs[-1][:10] != pubs[0][:10]
                                                         else "") + ")") if pubs else ""
            summary["note"] = (f"checked {summary['checked']} advisories first seen by the watch{span} against "
                               f"{len(graphs)} tenant fleet(s)" if advs
                               else "no npm MAL advisories added or modified in OSV since the last poll")
        try:
            summary["backfilled"] = _backfill(graphs, poll_id)
        except GraphUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - a backfill problem never fails the poll itself
            summary["backfilled"] = {}
            summary["backfill_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        summary["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        _write_poll(graphs, summary)
        BUS.publish("poll", {"poll": summary})
        LAST["poll"] = summary
        return summary
    finally:
        LAST["running"] = False
        _release_lock(token)
        _poll_lock.release()


def _backfill(graphs: dict[str, str], poll_id: str, limit: int = MAX_BATCH) -> dict[str, int]:
    """Per-tenant backfill: advisories the watch already ingested (in any tenant graph) that a tenant has no real
    verdict for, for example because the tenant was loaded after the poll that found them, are ingested into that
    tenant and checked against its fleet. Runs on every poll, also when OSV answers 304. Records come from OSV
    (fallback: the graph's own copy). Each backfilled tenant gets its own WatchPoll (source "backfill")."""
    known: dict[str, dict] = {}
    for g in graphs.values():
        for r in GraphRun(g).run("watch_ingested_advisories", {"limit": limit}):
            known.setdefault(r["id"], r)
    if not known:
        return {}
    ids = list(known)
    missing = {t: [i for i in ids if i not in {r["id"] for r in GraphRun(g).run("watch_checked_ids", {"ids": ids})}]
               for t, g in graphs.items()}
    missing = {t: m[:limit] for t, m in missing.items() if m}
    if not missing:
        return {}
    need = sorted({i for m in missing.values() for i in m})
    recs, _errs = osv.fetch_records(need, deadline_s=DEADLINE_S)
    advs_by_id: dict[str, list[osv.Advisory]] = {}
    for i in need:
        advs = osv.normalize(recs[i]) if i in recs else []
        if not advs:  # OSV unreachable: the graph's own copy of the record
            k = known[i]
            advs = [osv.Advisory(id=i, pkg=k["pkg"], bad_versions=sorted(k.get("bad_versions") or []),
                                 all_versions=bool(k.get("all_versions")), aliases=k.get("aliases") or [],
                                 summary=k.get("summary") or "", published=k.get("published"),
                                 modified=k.get("modified"), url=k.get("url") or "")]
        advs_by_id[i] = advs
    out: dict[str, int] = {}
    for t, m in missing.items():
        advs = [a for i in m for a in advs_by_id.get(i, [])]
        bid = f"{poll_id}-bf-{t}"
        events, _ = _record({t: graphs[t]}, advs, poll_id=bid, replay=False, ingest=True)
        for e in events:
            BUS.publish("event", e)
        pubs = sorted(a.published for a in advs if a.published)
        gr = GraphRun(graphs[t])
        gr.run("watch_write_poll", {"poll": {
            "id": bid, "ts": now_iso(), "source": "backfill", "checked": len(advs), "new_advisories": 0,
            "already_known": len(m), "index_entries": 0, "hits": sum(1 for e in events if e["verdict"] == "hit"),
            "ms": 0, "errors": 0, "published_oldest": pubs[0] if pubs else None,
            "published_newest": pubs[-1] if pubs else None,
            "note": f"backfill: checked {len(advs)} advisories the watch ingested before this tenant had verdicts"}},
            write=True)
        out[t] = len(advs)
    return out


def replay(query: str = "debug@4.4.2", tenant_graphs_override: dict[str, str] | None = None) -> dict:
    """Re-play a PAST real advisory through the watcher pipeline (fetch OSV record, normalize, check, record).

    Nothing new is ingested (the advisory is already in the graph); the WatchEvents are stored with replay=true."""
    t0 = time.perf_counter()
    q = (query or "").strip()[:80]
    graphs = tenant_graphs_override or tenant_graphs()
    first = next(iter(graphs.values()), None)
    if not first:
        raise GraphUnavailable("graph unavailable: no tenant graph loaded")
    found = GraphRun(first).run("watch_replay_lookup", {"q": q})
    if not found:
        raise ValueError(f"no advisory in the graph for {q!r}; try debug@4.4.2, chalk@5.6.1 or GHSA-4x49-vf9v-38px")
    mal = found[0]["id"]
    source = "osv-live"
    try:
        rec = osv.fetch_record(mal)
    except Exception:  # noqa: BLE001 - offline: the committed copy of the same OSV record
        rec, source = osv.cached_record(mal), "osv-cache"
    advs = osv.normalize(rec) if rec else []
    if not advs:  # last resort: the graph's own copy of the record
        f = found[0]
        advs = [osv.Advisory(id=mal, pkg=f["pkg"], bad_versions=sorted(f["bad_versions"]), all_versions=False,
                             aliases=f.get("aliases") or [], summary=f.get("summary") or "", published=f.get("published"),
                             url=f.get("url") or "")]
        source = "graph"
    poll_id = "R-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
    events, _ = _record(graphs, advs, poll_id=poll_id, replay=True, ingest=False)
    now = datetime.now(timezone.utc)
    for e in events:
        _with_age(e, {}, now)
        BUS.publish("event", e)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    summary = {"id": poll_id, "ts": now_iso(), "source": "replay", "replay": True, "record_source": source,
               "advisory": mal, "query": q, "checked": len(advs), "hits": sum(1 for e in events if e["verdict"] == "hit"),
               "ms": ms, "events": events,
               "note": f"REPLAY of a past real advisory ({mal}, published {advs[0].published}); not a new alert"}
    BUS.publish("poll", {"poll": {k: v for k, v in summary.items() if k != "events"}})
    return summary


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 3600:
        return f"{max(1, s // 60)} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def _utc(v) -> str:
    d = _parse_ts(v)
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "unknown"


def _with_age(ev: dict, row: dict, now: datetime) -> dict:
    """Honest age fields: when OSV published (and last modified) the advisory, and when this watch first saw it.
    An advisory published days ago that the watch only just picked up is "first seen", never "new"."""
    ev["published"] = ev.get("published") or row.get("a_published")
    ev["modified"] = ev.get("modified") or row.get("a_modified")
    first_seen = row.get("a_ingested") if row.get("a_ingested") else (None if ev.get("replay") else ev.get("ts"))
    ev["first_seen_by_watch"] = first_seen
    pub = _parse_ts(ev["published"])
    ev["published_age_s"] = int((now - pub).total_seconds()) if pub else None
    ev["published_ago"] = _ago(ev["published_age_s"]) if pub else None
    if ev.get("replay"):
        n = int(row.get("replay_count") or 1)
        ev.update(replay_count=n, first_replayed=row.get("first_replayed") or ev.get("ts"),
                  last_replayed=row.get("last_replayed") or ev.get("ts"))
        ev["age_label"] = (f"replay of a past advisory published {ev['published_ago'] or 'at an unknown time'}"
                           + (f" ({ev['published'][:10]})" if ev["published"] else "")
                           + (f"; replayed {n} times, last at {_utc(ev['last_replayed'])}" if n > 1
                              else f"; replayed at {_utc(ev['last_replayed'])}"))
    else:
        ev["age_label"] = (f"published {ev['published_ago'] or 'at an unknown time'}; "
                           f"first seen by watch at {_utc(first_seen)}")
    return ev


def feed(tenant: str, limit: int = 50, graphs: dict[str, str] | None = None) -> dict:
    """Recent WatchEvents for one tenant: real ones newest first, then replays collapsed to one row per replayed
    advisory (latest replay, with replay_count). Each event carries published, modified, first_seen_by_watch,
    published_ago and age_label."""
    graphs = graphs or tenant_graphs()
    if tenant not in graphs:
        raise ValueError(f"unknown tenant {tenant!r}")
    gr = GraphRun(graphs[tenant])
    limit = max(1, min(200, int(limit)))
    now = datetime.now(timezone.utc)
    real = [_with_age(dict(r["event"]), r, now) for r in gr.run("watch_feed", {"limit": limit})]
    groups: dict[tuple, dict] = {}
    for r in gr.run("watch_feed_replays", {"limit": KEEP_EVENTS}):  # newest first
        e = r["event"]
        g = groups.get((e.get("advisory"), e.get("package")))
        if g is None:
            groups[(e.get("advisory"), e.get("package"))] = g = {**r, "event": dict(e), "replay_count": 0,
                                                                  "last_replayed": e.get("ts")}
        g["replay_count"] += 1
        g["first_replayed"] = e.get("ts")
    reps = [_with_age(g["event"], g, now) for g in list(groups.values())[:limit]]
    evs = sorted(real + reps, key=lambda e: e.get("ts") or "", reverse=True)[:limit]
    polls = [r["poll"] for r in gr.run("watch_polls", {"limit": 5})]
    counts = (gr.run("watch_counts") or [{}])[0]
    counts = {k: int(v or 0) for k, v in counts.items()}
    counts["replayed_advisories"] = len(groups)
    return {"tenant": tenant, "graph": graphs[tenant], "events": evs, "polls": polls,
            "counts": counts, "last_poll": polls[0] if polls else None,
            "running": LAST["running"], "loop": LAST["loop"], "trace": gr.steps}


# ----------------------------------------------------------------------------- background loop
_thread: threading.Thread | None = None
_stop = threading.Event()


def _loop() -> None:
    if _stop.wait(START_DELAY_S):
        return
    while not _stop.is_set():
        try:
            s = poll(source="scheduled")
            log.info("watch poll %s: %s in %s ms", s["id"], s.get("note"), s.get("ms"))
        except RuntimeError as e:
            log.info("watch poll skipped: %s", e)
        except Exception as e:  # noqa: BLE001 - never let the loop die
            log.warning("watch poll failed: %s: %s", type(e).__name__, e)
            LAST["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        LAST["loop"]["next_at"] = time.time() + INTERVAL_S
        if _stop.wait(INTERVAL_S):
            return


def start_background() -> bool:
    """Start the scheduled poller (daemon thread). Off under pytest and when WATCH_INTERVAL_S is 0."""
    global _thread
    import sys
    if INTERVAL_S <= 0 or "pytest" in sys.modules or os.getenv("WATCH_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="osv-watch", daemon=True)
    _thread.start()
    LAST["loop"] = {"enabled": True, "interval_s": INTERVAL_S, "next_at": time.time() + START_DELAY_S}
    return True
