"""BYO scan: build an isolated FalkorDB graph for one project and run the incident exposure tools on it."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .. import config, db, tools
from ..router import graph_elements
from ..db import CATALOGUE, GraphUnavailable, Trace
from . import sources
from .sources import ByoError

PREFIX = "byo:"
TTL_S = int(__import__("os").getenv("BYO_TTL_SECONDS", str(6 * 3600)))
MAX_GRAPHS = int(__import__("os").getenv("BYO_MAX_GRAPHS", "50"))
MAX_DIRECT = 60
DEFAULT_DEPTH = 6
ATTACK_DAY = "2025-09-08T00:00:00Z"
_lock = threading.Lock()


class GraphTrace(Trace):
    """Same trace format as the main app, but bound to a named graph (a BYO tenant or fleet:acme)."""

    def __init__(self, graph_name: str):
        super().__init__()
        self.graph_name = graph_name

    def run(self, tool: str, query: str, params: dict | None = None, *, write: bool = False,
            note: str | None = None) -> list[dict]:
        cypher = CATALOGUE[query]["cypher"]
        params = params or {}
        t0 = time.perf_counter()
        try:
            g = db.client().select_graph(self.graph_name)
            res = g.query(cypher, params) if write else g.ro_query(cypher, params)
        except Exception as e:  # noqa: BLE001
            ms = round((time.perf_counter() - t0) * 1000, 2)
            self.steps.append(dict(tool=tool, query=query, params=db._short(params), ms=ms, rows=0, error=str(e),
                                   file=CATALOGUE[query]["file"], graph=self.graph_name))
            if db._is_conn_error(e):
                raise GraphUnavailable(f"graph unavailable: {e}") from e
            raise
        ms = round((time.perf_counter() - t0) * 1000, 2)
        header = [h[1] if isinstance(h, (list, tuple)) else h for h in (res.header or [])]
        rows = [dict(zip(header, r)) for r in res.result_set]
        step = dict(tool=tool, query=query, params=db._short(params), ms=ms, rows=len(rows), write=write,
                    file=CATALOGUE[query]["file"], graph=self.graph_name)
        if note:
            step["note"] = note
        self.steps.append(step)
        return rows


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(rows: list, n: int = 400):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def direct_deps(pkg: dict, include_dev: bool) -> tuple[list[tuple[str, str, bool]], list[str]]:
    deps: list[tuple[str, str, bool]] = []
    seen = set()
    for key, dev in (("dependencies", False), ("devDependencies", True)):
        if dev and not include_dev:
            continue
        for name, req in (pkg.get(key) or {}).items():
            if not isinstance(name, str) or not re.match(r"^(@[a-z0-9._~-]+/)?[a-z0-9._~-]{1,214}$", name, re.I):
                continue
            if name in seen:
                continue
            seen.add(name)
            deps.append((name, str(req)[:100], dev))
    skipped = [f"{n} (cap of {MAX_DIRECT} direct dependencies)" for n, _, _ in deps[MAX_DIRECT:]]
    return deps[:MAX_DIRECT], skipped


# --------------------------------------------------------------------------- graph lifecycle
def tenant_prefix(tenant: str | None = None) -> str:
    """BYO scan graphs are scoped per tenant: byo:<tenant>:<hash>. A scan made in Acme is never listed in Globex."""
    from .. import tenants
    return f"{PREFIX}{tenants.validate(tenant or tenants.current())}:"


def list_byo_graphs(tenant: str | None = None, *, all_tenants: bool = False) -> list[str]:
    """This tenant's scan graphs (default), or every BYO graph including legacy unscoped byo:<hash> ones."""
    want = PREFIX if all_tenants else tenant_prefix(tenant)
    try:
        return [g for g in db.client().list_graphs() if g.startswith(want)]
    except Exception as e:  # noqa: BLE001
        if db._is_conn_error(e):
            raise GraphUnavailable(f"graph unavailable: {e}") from e
        raise


def scan_meta(graph_name: str) -> dict | None:
    rows = GraphTrace(graph_name).run("byo_scan", "byo_scan_meta")
    return rows[0]["scan"] if rows and rows[0].get("scan") else None


def delete_graph(graph_name: str) -> None:
    if not graph_name.startswith(PREFIX):
        raise ValueError("refusing to delete a non-BYO graph")
    try:
        db.client().select_graph(graph_name).delete()
    except Exception as e:  # noqa: BLE001
        if db._is_conn_error(e):
            raise GraphUnavailable(f"graph unavailable: {e}") from e


def cleanup(now: float | None = None) -> list[str]:
    """Delete expired BYO graphs, then the oldest ones beyond MAX_GRAPHS. Returns deleted names."""
    now = now or _now()
    deleted, live = [], []
    for name in list_byo_graphs(all_tenants=True):
        try:
            m = scan_meta(name)
        except GraphUnavailable:
            raise
        except Exception:  # noqa: BLE001
            m = None
        if not m or float(m.get("expires_at") or 0) < now:
            delete_graph(name)
            deleted.append(name)
        else:
            live.append((float(m.get("created_at") or 0), name))
    live.sort()
    while len(live) > MAX_GRAPHS:
        _, name = live.pop(0)
        delete_graph(name)
        deleted.append(name)
    return deleted


def _scan_id(source: str, pkg: dict, lock_present: bool, include_dev: bool) -> str:
    body = json.dumps({"s": source, "d": pkg.get("dependencies"), "dd": pkg.get("devDependencies"),
                       "l": lock_present, "dev": include_dev, "v": 1}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def _export_incident() -> list[dict]:
    from .. import tenants
    g = tenants.graph_name()
    rows = GraphTrace(g).run("byo_scan", "byo_export_incident")
    if not rows:
        raise ByoError(503, "incident_not_loaded",
                       f"The incident data is not loaded in '{g}'. Run ingest/load.py first.")
    return rows


def build_graph(graph_name: str, service: str, meta: dict, resolved: list[sources.Resolved],
                incident: list[dict], tr: GraphTrace) -> dict:
    versions: dict[str, dict] = {}
    requires: dict[tuple[str, str], str] = {}
    direct = []
    for r in resolved:
        if not r.graph:
            continue
        keys = []
        for n in r.graph.get("nodes", []):
            vk = n["versionKey"]
            k = f"{vk['name']}@{vk['version']}"
            keys.append(k)
            versions.setdefault(k, dict(key=k, pkg=vk["name"], version=vk["version"], unpublished=False,
                                        malicious=False))
        if not keys:
            continue
        direct.append(dict(key=keys[0], requirement=r.requirement, dev=r.dev))
        for e in r.graph.get("edges", []):
            requires[(keys[e["fromNode"]], keys[e["toNode"]])] = e["requirement"]
    adv_rows, rights = [], []
    for row in incident:
        a, v = row["advisory"], row["version"]
        versions[v["key"]] = dict(key=v["key"], pkg=v["pkg"], version=v["version"], unpublished=True, malicious=True)
        adv_rows.append({**{k: a.get(k) for k in ("id", "ids", "aliases", "ghsa", "cve", "summary", "published",
                                                   "url", "pkg", "bad_version", "fixed_version")}, "key": v["key"]})
        if row.get("maintainer"):
            rights.append(dict(npm=row["maintainer"], name=row.get("maintainer_name"),
                               github=row.get("maintainer_github"), pkg=v["pkg"], as_of=row.get("as_of"),
                               source=row.get("source"), incident=row.get("incident")))

    try:
        db.client().select_graph(graph_name).delete()
    except Exception:  # noqa: BLE001
        pass
    for q in ("byo_index_package", "byo_index_version_key", "byo_index_version_pkg", "byo_index_service",
              "byo_index_advisory", "byo_index_maintainer"):
        tr.run("byo_scan", q, write=True)
    tr.run("byo_scan", "byo_create_scan", {**meta, "service": service}, write=True)
    pkgs = sorted({v["pkg"] for v in versions.values()})
    for c in _chunks([{"name": p} for p in pkgs]):
        tr.run("byo_scan", "byo_create_packages", {"rows": c}, write=True)
    for c in _chunks(list(versions.values())):
        tr.run("byo_scan", "byo_create_versions", {"rows": c}, write=True)
    if direct:
        tr.run("byo_scan", "byo_create_depends_on", {"service": service, "rows": direct}, write=True)
    for c in _chunks([{"a": a, "b": b, "req": q} for (a, b), q in requires.items()]):
        tr.run("byo_scan", "byo_create_requires", {"rows": c}, write=True)
    tr.run("byo_scan", "byo_create_advisories", {"rows": adv_rows}, write=True)
    if rights:
        tr.run("byo_scan", "byo_create_publish_rights", {"rows": rights}, write=True)
    tr.compact("byo_scan")
    return dict(packages=len(pkgs), versions=len(versions), requires=len(requires), direct=len(direct),
                advisories=len(adv_rows))


# --------------------------------------------------------------------------- results
def _is_dev(path: dict, dev_keys: set) -> bool:
    return len(path["via"]) > 1 and path["via"][1] in dev_keys


def _name(node: str) -> str:
    """'https-proxy-agent@5.0.1' -> 'https-proxy-agent', '@commitlint/cli@19.0.0' -> '@commitlint/cli'."""
    return node.rsplit("@", 1)[0] if "@" in node[1:] else node


PAYLOAD_NOTE = ("The Sept 2025 payload hooked browser crypto-wallet APIs, so runtime and bundled code matter most; "
                "dev-only exposure hits developer and CI machines.")


def verdicts(exposed: list[dict], include_dev: bool) -> tuple[dict, dict]:
    """Runtime first: structured verdicts the UI renders, runtime and dev tooling kept apart."""
    run_items = [x for x in exposed if x.get("runtime_vias")]
    dev_items = [x for x in exposed if x.get("dev_path_count")]
    run_pkgs = sorted({f"{x['package']}@{x['bad_version']}" for x in run_items})
    run_paths = [dict(package=f"{x['package']}@{x['bad_version']}", via=v,
                      chain=" → ".join(_name(n) for n in v)) for x in run_items for v in x["runtime_vias"]]
    run_paths.sort(key=lambda p: (len(p["via"]), p["package"]))
    vr = dict(status="exposed" if run_items else "clean", packages=run_pkgs, path_count=len(run_paths),
              paths=run_paths[:20])
    if run_items:
        first = run_paths[0]
        more = len(run_paths) - 1
        vr["text"] = (f"Runtime: exposed via {first['chain']} ({', '.join(run_pkgs)})"
                      + (f", plus {more} more runtime path{'s' if more != 1 else ''}" if more else "") + ".")
    else:
        vr["text"] = "Runtime clean."
    dev_pkgs = sorted({f"{x['package']}@{x['bad_version']}" for x in dev_items})
    dev_paths = sum(x.get("dev_path_count", 0) for x in dev_items)
    entries = sorted({e for x in dev_items for e in x.get("dev_entries", [])})
    if not include_dev:
        vd = dict(status="not_scanned", packages=[], path_count=0, via=[],
                  text="Dev tooling: not scanned (devDependencies excluded).")
    elif dev_items:
        via = ", ".join(entries[:4]) + (f" and {len(entries) - 4} more" if len(entries) > 4 else "")
        lead = "Dev tooling" if run_items else "Dev tooling only"
        vd = dict(status="exposed", packages=dev_pkgs, path_count=dev_paths, via=entries,
                  text=f"{lead}: {dev_paths}{' more' if run_items else ''} path{'s' if dev_paths != 1 else ''} to "
                       f"{', '.join(dev_pkgs)} via {via}.")
    else:
        vd = dict(status="clean", packages=[], path_count=0, via=[], text="Dev tooling clean.")
    return vr, vd


def _dev_only(f: dict, dev_keys: set) -> bool:
    """Dev-only exposure means EVERY exposed path enters through a devDependency (one runtime path is enough)."""
    paths = f.get("exposed_paths") or [f["path"], *f.get("alternatives", [])]
    paths = [p for p in paths if p.get("exposed")] or [f["path"]]
    return all(_is_dev(p, dev_keys) for p in paths)


def _flatten(result: dict, locked: dict[str, list[str]], has_lock: bool, dev_keys: set) -> tuple[list, list]:
    exposed, not_exposed = [], []
    for bucket in (result["exposed"], result["safe"]):
        for e in bucket:
            for f in e["findings"]:
                p = f["path"]
                item = dict(package=f["package"], bad_version=f["bad_version"], clean_version=f.get("clean_version"),
                            advisory_id=f.get("advisory_id"), advisory=f.get("advisory_id"), ghsa=f.get("ghsa"),
                            path=p, alternatives=f.get("alternatives", []), package_path=p["via"],
                            requirement=p["requirement"], hops=p["hops"], reason=p["reason"],
                            path_count=f["path_count"], exposed_path_count=f.get("exposed_path_count", 0),
                            via_dev_dependency=_dev_only(f, dev_keys))
                if f["exposed"]:
                    eps = f.get("exposed_paths") or [p]
                    item["runtime_paths"] = [x["display"] for x in eps if not _is_dev(x, dev_keys)]
                    devp = [x["display"] for x in eps if _is_dev(x, dev_keys)]
                    item["runtime_vias"] = [x["via"] for x in eps if not _is_dev(x, dev_keys)]
                    item["dev_entries"] = sorted({_name(x["via"][1]) for x in eps if _is_dev(x, dev_keys)})
                    item["dev_paths"], item["dev_path_count"] = devp[:10], len(devp)
                if has_lock:
                    lv = locked.get(f["package"], [])
                    item["locked_versions"] = lv
                    item["lockfile_has_bad_version"] = f["bad_version"] in lv
                    if f["exposed"]:
                        item["lockfile_note"] = (
                            f"Today's package-lock.json pins {f['package']} to {', '.join(lv)}. "
                            + ("That IS the malicious version: treat this checkout as compromised."
                               if f["bad_version"] in lv else
                               f"`npm ci` with this lockfile would not install {f['bad_version']}, but on Sept 8 2025 "
                               f"a fresh `npm install` (no lockfile, or a lockfile refresh) would have resolved "
                               f"{f['bad_version']} because {p['requirement']} admits it.")
                            if lv else f"{f['package']} is not in today's package-lock.json (it may be stale or partial).")
                (exposed if f["exposed"] else not_exposed).append(item)
    exposed.sort(key=lambda x: (x["hops"], x["package"]))
    not_exposed.sort(key=lambda x: (x["package"], x["hops"]))
    return exposed, not_exposed


def run_scan(*, github_url: str | None = None, package_json: str | None = None, include_dev: bool = True,
             depth: int = DEFAULT_DEPTH) -> dict[str, Any]:
    t_start = time.perf_counter()
    depth = max(1, min(config.MAX_DEPTH, int(depth)))
    if bool(github_url) == bool(package_json):
        raise ByoError(400, "bad_request", "Send exactly one of github_url or package_json")
    if github_url:
        files = sources.fetch_github(github_url)
        pkg, lock = files.package_json, files.lockfile
        repo = f"github.com/{files.owner}/{files.repo}"
        ref, source = files.ref, f"github:{files.owner}/{files.repo}@{files.ref}"
        service = f"{files.owner}/{files.repo}".lower()
        warnings = [files.lockfile_skipped] if files.lockfile_skipped else []
    else:
        pkg = sources.parse_package_json(package_json)
        lock, files = None, None
        repo, ref, source = None, None, "pasted package.json"
        service = str(pkg.get("name") or "pasted-package-json").lower()[:80]
        warnings = []
    if pkg.get("workspaces"):
        warnings.append("Monorepo detected (workspaces): only the root package.json was scanned.")

    deps, skipped = direct_deps(pkg, include_dev)
    if skipped:
        warnings.append(f"Only the first {MAX_DIRECT} of {MAX_DIRECT + len(skipped)} direct dependencies were "
                        f"scanned ({len(skipped)} skipped, devDependencies last). A 'No' may miss exposure "
                        f"through the skipped ones.")
    if not deps:
        raise ByoError(422, "no_dependencies",
                       "package.json has no dependencies" + ("" if include_dev else " (devDependencies excluded)"))
    scan_id = _scan_id(source if github_url else "pasted", pkg, lock is not None, include_dev)
    graph_name = tenant_prefix() + scan_id

    # Graph must be up before we spend time on the network (hard error, never a fallback).
    incident = _export_incident()
    try:
        cleanup()
    except GraphUnavailable:
        raise
    except Exception:  # noqa: BLE001
        pass

    tr = GraphTrace(graph_name)
    t0 = time.perf_counter()
    resolved = sources.resolve_direct(deps, ATTACK_DAY)
    tr.add("deps.dev", f"resolved {sum(1 for r in resolved if r.graph)}/{len(resolved)} direct dependencies",
           ms=round((time.perf_counter() - t0) * 1000, 2), rows=len(resolved))
    now = _now()
    meta = dict(id=scan_id, created_at=now, expires_at=now + TTL_S, source=source, repo=repo or "", ref=ref or "",
                as_of=ATTACK_DAY, direct_count=len(deps), has_lockfile=lock is not None)
    with _lock:
        counts = build_graph(graph_name, service, meta, resolved, incident, tr)

    scope = tools.incident_scope(tr, maintainer=incident[0].get("maintainer") or "qix")
    fleet = [dict(service=service, tier=1, team=None)]
    dev_keys = {f"{r.name}@{r.version}" for r in resolved if r.dev and r.version}
    # Runtime paths win the primary slot over dev paths of the same length (P1-A).
    result = tools.exposure(tr, scope, depth, fleet=fleet, prefer=lambda p: _is_dev(p, dev_keys),
                            keep_exposed_paths=True)
    locked = sources.lockfile_versions(lock)
    exposed, not_exposed = _flatten(result, locked, lock is not None, dev_keys)

    bad_pkgs = sorted({f"{x['package']}@{x['bad_version']}" for x in exposed})
    unresolved = [dict(package=r.name, requirement=r.requirement, error=r.error) for r in resolved if r.error]
    prod_hit = sorted({f"{x['package']}@{x['bad_version']}" for x in exposed if not x["via_dev_dependency"]})
    runtime_paths = [p for x in exposed for p in x.get("runtime_paths", [])]
    v_run, v_dev = verdicts(exposed, include_dev)
    if exposed:
        headline = (f"{v_run['text']} {v_dev['text']}" if v_run["status"] == "exposed"
                    else f"Runtime clean. {v_dev['text']}")
    elif not_exposed:
        headline = (f"No: {repo or service} depends on {len({x['package'] for x in not_exposed})} of the affected "
                    f"packages, but no declared range admits a malicious version.")
    else:
        headline = f"No: none of the 19 affected packages appear in {repo or service}'s dependency tree (depth {depth})."
    if skipped:
        headline += (f" Caveat: {len(skipped)} of {MAX_DIRECT + len(skipped)} direct dependencies were not "
                     f"scanned (cap of {MAX_DIRECT}).")
    lock_line = None
    if lock is not None:
        hit = sorted({x["package"] for x in exposed if x.get("lockfile_has_bad_version")})
        lock_line = ("package-lock.json contains a malicious version: " + ", ".join(hit)) if hit else \
            "package-lock.json was read: it does not pin any of the malicious versions."
    summary = dict(headline=headline, would_have_been_hit=bool(exposed), runtime_hit=v_run["status"] == "exposed",
                   verdict_runtime=v_run, verdict_dev=v_dev, payload_note=PAYLOAD_NOTE, runtime_exposed=prod_hit,
                   runtime_paths=runtime_paths, exposed_count=len(exposed),
                   not_exposed_count=len(not_exposed), malicious_versions=bad_pkgs, depth=depth,
                   direct_dependencies=len(deps), skipped_direct=len(skipped), resolved_direct=counts["direct"], lockfile=lock_line,
                   assumption="Exposure = the semver range on the last edge admits the malicious version "
                              "(what a fresh install on Sept 8 2025 would resolve). Trees are deps.dev "
                              "resolutions; direct deps use the newest matching version published before the "
                              "attack.")
    fix = tools.build_fix(result, [], dev_direct={r.name for r in resolved if r.dev})
    patch = fix["per_service"][0]["package_json"] if fix["per_service"] else {"overrides": {}}
    viz = graph_elements(result["exposed"], result["safe"])
    stats = dict(direct=counts["direct"], direct_declared=len(deps), versions=counts["versions"],
                 edges=counts["requires"] + counts["direct"], packages=counts["packages"],
                 ms=round((time.perf_counter() - t_start) * 1000, 1))
    return dict(scan_id=graph_name, scan_hash=scan_id, graph_name=graph_name, repo=repo, service=service,
                source=dict(kind="github" if github_url else "package_json", label=repo or service, ref=ref),
                outcome="answered_with_path" if exposed else "not_in_graph",
                summary=headline, details=summary, exposed=exposed, not_exposed=not_exposed,
                overrides=patch, graph=viz, subgraph=viz, trace=tr.steps, stats=stats,
                unresolved=unresolved, skipped=skipped, warnings=warnings,
                created_at=_iso(now), expires_at=_iso(now + TTL_S))
