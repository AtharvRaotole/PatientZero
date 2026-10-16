"""Router support for watched advisories where every published version of a package is malicious.

OSV marks such malware with a range that starts at 0 and never ends, so there is no single bad version to check a
range against: any dependency path to the package is exposed. all_versions_scope() turns that into a normal
incident scope (one entry per version a fleet path resolves today), so the usual exposure traversal, who to wake and
fix flow run unchanged, and supplies a clear lead sentence.
"""
from __future__ import annotations

from .. import tools
from ..db import Trace


def all_versions_scope(tr: Trace, ent: dict, kind: str, key: str, depth: int) -> tuple[list[dict], dict | None]:
    pkgs = sorted(set(ent.get("packages") or []))
    if kind == "version" and ent.get("package_versions"):
        pkgs = sorted({pv["package"] for pv in ent["package_versions"]} | set(pkgs))
    advisory = key if kind == "advisory" else ""
    if not pkgs and not advisory:
        return [], None
    rows = tr.run("watch", "watch_all_versions", {"advisory": advisory, "pkgs": pkgs},
                  note="watched advisories where every published version is malicious")
    if not rows:
        return [], None
    adv_by_pkg: dict[str, dict] = {}
    for r in rows:
        adv_by_pkg.setdefault(r["pkg"], r)
    names = sorted(adv_by_pkg)
    reach = tools.exposure_rows(tr, names, depth)
    scope = []
    seen: set[tuple[str, str]] = set()
    for r in reach:
        k = (r["pkg"], str(r["resolved_today"]))
        if r["pkg"] in adv_by_pkg and r.get("resolved_today") and k not in seen:
            seen.add(k)
            a = adv_by_pkg[r["pkg"]]
            scope.append(dict(package=r["pkg"], bad_version=str(r["resolved_today"]), clean_version=None,
                              advisory_id=a["advisory"], ghsa=a.get("ghsa"), cve=None, aliases=a.get("aliases") or [],
                              url=a.get("url"), source="OSV: every published version is malicious",
                              as_of=a.get("published")))
    ids = sorted({a["advisory"] for a in adv_by_pkg.values()})
    what = ", ".join(names)
    lead = (f"Every published version of {what} is malicious ({', '.join(ids)}, OSV), so any dependency path is "
            f"exposed.")
    fleet_size = len(tools.fleet_overview(tr))
    services = sorted({r["service"] for r in reach if r["pkg"] in adv_by_pkg})
    if services:
        msg = f"{lead} {len(services)} of {fleet_size} services depend on it within {depth} hops."
    else:
        msg = (f"{lead} No fleet service depends on {what} within {depth} hops, so none of the {fleet_size} "
               f"services is hit.")
    return scope, {"packages": names, "advisories": ids, "lead": lead, "message": msg, "services": services,
                   "fleet_size": fleet_size}
