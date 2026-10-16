"""The fixed tool catalogue. Every tool runs human-written, parameterised Cypher from queries/*.cypher.

No tool ever builds Cypher from user text. Inputs are resolved against the graph (resolve_entity) first.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from . import config
from .db import Trace
from .semver_util import check

ID_RE = re.compile(r"\b(GHSA(?:-[0-9a-z]{4}){3}|MAL-\d{4}-\d+|CVE-\d{4}-\d+)\b", re.I)
PKGVER_RE = re.compile(r"((?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*)@(\d+\.\d+\.\d+(?:-[\w.]+)?)", re.I)
TOKEN_RE = re.compile(r"@?[a-z0-9][a-z0-9._/-]*", re.I)
MAX_TOKENS = 12

# English words that also happen to be npm package names: never treat them as entities.
STOPWORDS = set("""a about after all an and any are as at be been before but by can check compromised could did do
does done exposed find fix for from get got had has have hit how i if in into is it its just last let list made
maintainer make me my new no not now of on once one or our out package packages path phished hacked pwned page
please run safe see service services set show so some tell test that the then there these they this through to
today us use version versions was we were what when where which who why will with would yes you your affected
attack incident alert owns own wake whom on-call oncall single point failure points depends depend
connected connect reach reaches pull pulls between again remember recall history past previous maintains
maintainers owner owners team teams fleet blast radius
mime util events buffer stream process assert url os fs http https crypto string object array function map date
error inherits wrappy""".split())


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_id(s: str) -> str:
    s = s.strip()
    return "GHSA-" + s[5:].lower() if s.upper().startswith("GHSA-") else s.upper()


def _resolve(tr: Trace, text: str, pkg: str = "", version: str = "") -> list[dict]:
    return tr.run("resolve_entity", "resolve_entity", {"text": text, "pkg": pkg, "version": version})


# --------------------------------------------------------------------------- resolve_entity
def resolve_entity(text: str, tr: Trace) -> dict:
    """Map free text to graph nodes (Maintainer, Advisory, Version, Package, Service)."""
    out: dict = dict(advisories=[], package_versions=[], maintainers=[], services=[], packages=[],
                     unresolved=[], incident_packages=[])
    for i in dict.fromkeys(norm_id(m) for m in ID_RE.findall(text)):
        rows = [r for r in _resolve(tr, i) if r["kind"] == "Advisory"]
        if rows:
            out["advisories"].append({"query": i, "id": rows[0]["key"], "label": rows[0]["label"]})
        else:
            out["unresolved"].append(i)
    for pkg, ver in dict.fromkeys((m.group(1).lower(), m.group(2)) for m in PKGVER_RE.finditer(text)):
        rows = _resolve(tr, pkg, pkg, ver)
        kinds = {r["kind"]: r for r in rows}
        if "Package" in kinds or "Version" in kinds:
            v = kinds.get("Version")
            out["package_versions"].append({"package": pkg, "version": ver, "key": f"{pkg}@{ver}",
                                            "in_graph": v is not None,
                                            "malicious": bool(v and v["in_incident"])})
        else:
            out["unresolved"].append(f"{pkg}@{ver}")
    rest = PKGVER_RE.sub(" ", ID_RE.sub(" ", text))
    tokens: list[str] = []
    for t in TOKEN_RE.findall(rest):
        t = t.strip("._-/").lower()
        if t.endswith("'s"):
            t = t[:-2]
        if len(t) < 2 or t in STOPWORDS or t.isdigit():
            continue
        tokens.append(t)
    out["unmatched"] = []
    for t in list(dict.fromkeys(tokens))[:MAX_TOKENS]:
        rows = _resolve(tr, t)
        if not rows and ("-" in t or "/" in t or "." in t):  # package-shaped token that is not in the graph
            out["unmatched"].append(t)
        for r in rows:
            if r["kind"] == "Maintainer":
                out["maintainers"].append(r["key"])
            elif r["kind"] == "Service":
                out["services"].append(r["key"])
            elif r["kind"] == "Package":
                out["packages"].append(r["key"])
                if r["in_incident"]:
                    out["incident_packages"].append(r["key"])
            elif r["kind"] == "Advisory":
                out["advisories"].append({"query": t, "id": r["key"], "label": r["label"]})
    tr.compact("resolve_entity")
    for k in ("maintainers", "services", "packages", "incident_packages"):
        out[k] = list(dict.fromkeys(out[k]))
    # A name that is both a service and a package (rare) is treated as the service.
    out["packages"] = [p for p in out["packages"] if p not in out["services"]]
    out["found"] = any(out[k] for k in ("advisories", "package_versions", "maintainers", "services", "packages"))
    return out


# --------------------------------------------------------------------------- incident_scope
def incident_scope(tr: Trace, *, maintainer: str | None = None, advisory_id: str | None = None,
                   packages: list[str] | None = None, package: str | None = None,
                   bad_version: str | None = None, campaign: str | None = None) -> list[dict]:
    """Bad versions in scope with clean versions. pkg@ver without an advisory is treated as user-asserted."""
    if campaign:
        rows = tr.run("incident_scope", "incident_scope_by_campaign", {"campaign": campaign})
    elif maintainer:
        rows = tr.run("incident_scope", "incident_scope_by_maintainer", {"maintainer": maintainer})
    elif advisory_id:
        rows = tr.run("incident_scope", "incident_scope_by_advisory", {"advisory_id": advisory_id})
    elif package and bad_version:
        rows = tr.run("incident_scope", "incident_scope_by_version", {"pkg": package, "version": bad_version})
        if not rows:  # in the graph but no advisory: trust the user's report, no clean version known
            rows = [dict(pkg=package, bad_version=bad_version, clean_version=None, advisory=None, ghsa=None,
                         cve=None, aliases=[], url=None, as_of=None, source="user report")]
    elif packages:
        rows = tr.run("incident_scope", "incident_scope_by_packages", {"pkgs": packages})
    else:
        rows = []
    return [dict(package=r["pkg"], bad_version=r["bad_version"], clean_version=r.get("clean_version"),
                 advisory_id=r.get("advisory"), ghsa=r.get("ghsa"), cve=r.get("cve"),
                 aliases=r.get("aliases") or [], url=r.get("url"), source=r.get("source"),
                 as_of=r.get("as_of")) for r in rows]


# --------------------------------------------------------------------------- exposure
def exposure_rows(tr: Trace, packages: list[str], depth: int) -> list[dict]:
    depth = clamp_depth(depth)
    if not packages:
        return []
    return tr.run("exposure", "exposure", {"pkgs": sorted(set(packages)), "depth": depth},
                  note=f"depth<={depth}; npm semver check on the last edge runs in the app")


def clamp_depth(depth) -> int:
    return max(1, min(config.MAX_DEPTH, int(depth)))


def exposure(tr: Trace, scope: list[dict], depth: int, fleet: list[dict] | None = None) -> dict:
    """Variable-length traversal + semver decision on the last edge. Returns per-service findings."""
    depth = clamp_depth(depth)
    bad: dict[str, list[dict]] = defaultdict(list)
    for s in scope:
        bad[s["package"]].append(s)
    rows = exposure_rows(tr, list(bad), depth)
    if fleet is None:
        fleet = fleet_overview(tr)
    tier = {f["service"]: f["tier"] for f in fleet}
    team = {f["service"]: f["team"] for f in fleet}

    per: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for r in rows:
        for sc in bad[r["pkg"]]:
            ok, reason = check(sc["bad_version"], r["requirement"], r["pkg"])
            path = dict(via=r["via"], requirements=r["requirements"], requirement=r["requirement"],
                        hops=r["hops"], resolved_today=r["resolved_today"], exposed=bool(ok), reason=reason,
                        display=display_path(r["via"], r["requirement"], r["pkg"], sc["bad_version"], ok))
            f = per[r["service"]].setdefault((r["pkg"], sc["bad_version"]), dict(
                package=r["pkg"], bad_version=sc["bad_version"], clean_version=sc.get("clean_version"),
                advisory_id=sc.get("advisory_id"), ghsa=sc.get("ghsa"), exposed=False, paths=[],
                path_count=0, exposed_path_count=0))
            f["path_count"] += 1
            if ok:
                f["exposed_path_count"] += 1
                f["exposed"] = True
            f["paths"].append(path)

    exposed, safe = [], []
    for svc, fmap in per.items():
        findings = []
        for f in fmap.values():
            ps = sorted(f.pop("paths"), key=lambda p: (not p["exposed"], p["hops"]))
            uniq, seen = [], set()
            for p in ps:
                if tuple(p["via"]) not in seen:
                    seen.add(tuple(p["via"]))
                    uniq.append(p)
            f["path"], f["alternatives"] = uniq[0], uniq[1:4]
            findings.append(f)
        findings.sort(key=lambda f: (not f["exposed"], f["path"]["hops"], f["package"]))
        entry = dict(service=svc, tier=tier.get(svc), team=team.get(svc), findings=findings,
                     min_hops=min(f["path"]["hops"] for f in findings),
                     exposed_packages=[f"{f['package']}@{f['bad_version']}" for f in findings if f["exposed"]])
        (exposed if entry["exposed_packages"] else safe).append(entry)
    exposed.sort(key=lambda e: (e["tier"] or 9, e["min_hops"], e["service"]))
    safe.sort(key=lambda e: (e["tier"] or 9, e["service"]))
    touched = {e["service"] for e in exposed + safe}
    clean = [f["service"] for f in fleet if f["service"] not in touched]
    return dict(depth=depth, exposed=exposed, safe=safe, clean=clean, fleet_size=len(fleet), path_rows=len(rows))


def display_path(via: list[str], req: str | None, pkg: str, bad: str, ok: bool | None) -> str:
    head = " → ".join(via[:-1])
    mark = f"⚠ {bad}" if ok else (f"✓ excludes {bad}" if ok is False else f"? {bad}")
    return f"{head} → {pkg} {req} {mark}"


def fleet_overview(tr: Trace) -> list[dict]:
    return tr.run("fleet_overview", "fleet_overview")


# --------------------------------------------------------------------------- who_to_wake
def who_to_wake(tr: Trace, services: list[str]) -> list[dict]:
    if not services:
        return []
    out = []
    for r in tr.run("who_to_wake", "who_to_wake", {"services": services}):
        oncall = [o for o in (r["oncall"] or []) if o and o.get("name")]
        members = [m for m in (r["members"] or []) if m and m.get("name")]
        out.append(dict(service=r["service"], tier=r["tier"], env=r.get("env"), repo=r["repo"], team=r["team"],
                        team_display=r["team_display"], slack=r["slack"], pager=r["pager"],
                        oncall=oncall, members=members))
    return out


def package_maintainers(tr: Trace, package: str) -> dict | None:
    rows = tr.run("who_to_wake", "package_maintainers", {"pkg": package})
    return rows[0] if rows else None


# --------------------------------------------------------------------------- single_points_of_failure
def single_points_of_failure(tr: Trace, limit: int = 10) -> dict:
    limit = max(1, min(20, int(limit)))
    try:
        m = tr.run("single_points_of_failure", "single_points_of_failure", {"limit": limit},
                   note="reach over DEPENDS_ON|REQUIRES*1..6 plus algo.pageRank('Version','REQUIRES')")
    except Exception as e:  # noqa: BLE001
        if is_unavailable(e):
            raise
        m = []
        tr.add("single_points_of_failure", f"maintainer ranking failed: {e}")
    pkgs = tr.run("single_points_of_failure", "package_reach", {"limit": limit})
    try:
        pr = tr.run("single_points_of_failure", "top_packages_pagerank", {"limit": limit})
    except Exception as e:  # noqa: BLE001
        if is_unavailable(e):
            raise
        pr = []
        tr.add("single_points_of_failure", f"algo.pageRank failed: {e}")
    return dict(maintainers=m, packages=pkgs, pagerank=pr)


def is_unavailable(e: Exception) -> bool:
    from .db import GraphUnavailable
    return isinstance(e, GraphUnavailable)


# --------------------------------------------------------------------------- path_between
def path_between(tr: Trace, service: str, package: str) -> dict | None:
    try:
        rows = tr.run("path_between", "path_between", {"service": service, "pkg": package},
                      note="algo.SPpaths")
    except Exception as e:  # noqa: BLE001
        if is_unavailable(e):
            raise
        tr.add("path_between", f"algo.SPpaths failed, falling back to shortestPath(): {e}")
        rows = tr.run("path_between", "path_between_shortestpath", {"service": service, "pkg": package})
    if not rows:
        return None
    r = rows[0]
    return dict(via=r["via"], requirements=r["requirements"], hops=int(r["hops"]))


# --------------------------------------------------------------------------- remediation_plan
def major_range(version: str) -> str:
    """npm range covering the compromised release line: '^6' for 6.2.2, '^0.2' for 0.2.3 (caret on 0.x is minor)."""
    parts = (version or "0").split("-")[0].split(".")
    return f"^{parts[0]}" if parts[0] != "0" else f"^0.{parts[1] if len(parts) > 1 else 0}"


def package_json_patch(findings: list[dict], dev_direct: set[str] | None = None) -> dict:
    """Install-safe package.json patch for one service.

    * A compromised package that is a DIRECT dependency gets its range bumped in dependencies (or
      devDependencies) to ^clean. npm rejects an override on a direct dependency (EOVERRIDE) unless both use the
      exact same spec, so the scoped override repeats "^clean" to also cover transitive copies. ("$pkg"
      references fail with "Unable to resolve reference" under a scoped key in npm 10, verified.)
    * A transitive-only package gets an override scoped to the compromised major, e.g. "ansi-styles@^6": "6.2.3",
      so other majors in the tree (chalk 4 needs ansi-styles 4, express 4 needs debug 2) are left alone.
    """
    deps: dict[str, str] = {}
    dev: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for f in findings:
        pkg, clean = f["package"], f.get("clean_version")
        if not clean:
            continue
        paths = [f["path"], *f.get("alternatives", [])]
        direct = any(len(p["via"]) == 2 and p.get("exposed") for p in paths)
        scope = f"{pkg}@{major_range(f['bad_version'])}"
        if direct:
            (dev if pkg in (dev_direct or set()) else deps)[pkg] = f"^{clean}"
            overrides[scope] = f"^{clean}"
        else:
            overrides[scope] = clean
    out: dict = {}
    if deps:
        out["dependencies"] = dict(sorted(deps.items()))
    if dev:
        out["devDependencies"] = dict(sorted(dev.items()))
    out["overrides"] = dict(sorted(overrides.items()))
    return out


def build_fix(result: dict, wake: list[dict], dev_direct: set[str] | None = None) -> dict:
    """Pure function: per-service install-safe package.json patch + notification drafts (no I/O)."""
    owners = {w["service"]: w for w in wake}
    per_service, combined, pins_all = [], {}, {}
    for e in result["exposed"]:
        ex = [f for f in e["findings"] if f["exposed"]]
        pins = {f["package"]: f["clean_version"] for f in ex if f.get("clean_version")}
        pins_all.update(pins)
        patch = package_json_patch(ex, dev_direct)
        combined.update({k: v for k, v in patch["overrides"].items() if not v.startswith("^")})
        w = owners.get(e["service"], {})
        oncall = w.get("oncall") or []
        oc = ", ".join(f"{o['name']} ({o['handle']})" for o in oncall) or "no on-call recorded"
        lines = [f"[PatientZero] {e['service']} (tier {e['tier']}) can install a malicious npm version.",
                 f"Owner: {w.get('team_display') or e.get('team')} {w.get('slack') or ''}".rstrip()
                 + f". On-call: {oc}."]
        for f in ex:
            ids = ", ".join(x for x in [f.get("advisory_id"), f.get("ghsa")] if x) or "user report"
            lines.append(f"* {f['package']}@{f['bad_version']} ({ids})")
            lines.append(f"  path: {f['path']['display']}")
        missing = [f["package"] for f in ex if not f.get("clean_version")]
        if missing:
            lines.append(f"No clean version on record for: {', '.join(missing)}. Pin manually.")
        lines.append("Action: merge this into package.json (direct dependencies are bumped, transitive ones are "
                     "overridden only on the compromised major), run npm install from a clean cache, redeploy, and "
                     "check CI logs for installs during the attack window.")
        lines.append(json.dumps(patch, indent=2))
        per_service.append(dict(service=e["service"], tier=e["tier"], team=w.get("team") or e.get("team"),
                                team_display=w.get("team_display"), slack=w.get("slack"), oncall=oncall,
                                pins=pins, overrides=patch, package_json=patch,
                                overrides_json=json.dumps(patch, indent=2),
                                draft="\n".join(lines), paths=[f["path"]["display"] for f in ex]))
    comb = {"overrides": dict(sorted(combined.items()))}
    return dict(per_service=per_service, combined=comb, combined_json=json.dumps(comb, indent=2),
                pins=dict(sorted(pins_all.items())),
                note="Fleet-wide block covers transitive installs only; services that depend on a compromised "
                     "package directly also need the dependency bump from their own patch.")


def remediation_plan(tr: Trace, *, trigger: str, entity_kind: str, entity_key: str, depth: int, result: dict,
                     wake: list[dict], session: str = "") -> dict:
    fix = build_fix(result, wake)
    at = now_iso()
    slug = re.sub(r"[^a-z0-9]+", "-", entity_key.lower()).strip("-")[:40]
    inc_id = f"INC-{at[:10]}-{slug}-{uuid.uuid4().hex[:6]}"
    patch = {p["service"]: p["pins"] for p in fix["per_service"]}
    tr.run("remediation_plan", "create_incident", dict(
        id=inc_id, at=at, trigger=trigger, entity_kind=entity_kind, entity_key=entity_key, depth=depth,
        exposed_count=len(result["exposed"]), patch=json.dumps(patch)), write=True)
    for key in entity_keys_for_link(entity_kind, entity_key):
        tr.run("remediation_plan", "link_incident_entity", {"id": inc_id, "entity_key": key}, write=True)
    tr.compact("remediation_plan", "link_incident_entity")
    # One EXPOSED edge per exposed service (AC9: edge count == exposed services), via its primary path.
    exp_rows, findings = [], []
    for e in result["exposed"]:
        f = next(f for f in e["findings"] if f["exposed"])
        exp_rows.append(dict(service=e["service"], path=f["path"]["via"], requirement=f["path"]["requirement"],
                             pkg=f["package"], advisory=f.get("advisory_id") or "", hops=f["path"]["hops"]))
        findings.append(dict(service=e["service"], text=(
            f"{e['service']} exposed to {', '.join(e['exposed_packages'])} at depth {depth}. "
            f"Patch: {json.dumps(patch.get(e['service'], {}))}")))
    if exp_rows:
        tr.run("remediation_plan", "link_exposed", {"id": inc_id, "rows": exp_rows}, write=True)
        tr.run("remediation_plan", "add_findings", {"id": inc_id, "at": at, "session": session, "rows": findings},
               write=True)
    fix.update(incident_id=inc_id, at=at, exposed_edges=len(exp_rows))
    # Procedural memory: reuse playbooks a human approved for earlier incidents on the same packages.
    from . import playbooks
    fix["playbooks"] = playbooks.apply_matching(tr, inc_id, fix["pins"])
    return fix


def entity_keys_for_link(kind: str, key: str) -> list[str]:
    if kind == "packages":
        return key.split(",")
    return [key]


# --------------------------------------------------------------------------- recall
def recall(tr: Trace, entity_key: str) -> list[dict]:
    rows = tr.run("recall", "recall", {"entity_key": entity_key})
    for r in rows:
        r["exposed"] = [x for x in (r.get("exposed") or []) if x and x.get("service")]
        try:
            r["patch"] = json.loads(r["patch"]) if r.get("patch") else {}
        except (TypeError, ValueError):
            pass
    return rows


def list_incidents(tr: Trace, limit: int = 20) -> list[dict]:
    return tr.run("recall", "list_incidents", {"limit": limit})
