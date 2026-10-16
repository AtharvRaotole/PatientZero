"""The 8 agent tools from SPEC section 6, with validated arguments. Shared by the HTTP API and the MCP server."""
from __future__ import annotations

from typing import Any

from . import tools
from .db import Trace

TOOL_DOCS = {
    "resolve_entity": "Map text (maintainer, GHSA/MAL/CVE id, package@version, package, service) to graph nodes.",
    "incident_scope": "Bad versions in scope (with clean versions) for a maintainer or an advisory id.",
    "exposure": "Variable-length paths Service->...->package up to depth 1..6, with an npm-semver exposed flag "
                "decided on the last edge's requirement.",
    "who_to_wake": "Owning team, on-call person and tier for a service.",
    "single_points_of_failure": "Maintainer accounts ranked by fleet reach, plus algo.pageRank over packages.",
    "path_between": "Shortest dependency path from a service to a package (algo.SPpaths).",
    "remediation_plan": "Re-run the investigation for a trigger, build npm overrides + notification drafts, and "
                        "write Incident/EXPOSED/Finding nodes back to the graph.",
    "recall": "Prior incidents about an entity, read from the graph (agent memory).",
    "package_maintainers": "Maintainer accounts that can publish a package (who to call upstream).",
}


def _wrap(tr: Trace, result: Any) -> dict:
    return {"result": result, "trace": tr.steps}


def resolve_entity(text: str) -> dict:
    tr = Trace()
    return _wrap(tr, tools.resolve_entity(text, tr))


MAX_LIST = 2000  # cap on list arguments (scope rows, packages, services) from any client


def _cap(name: str, xs: list | None) -> list | None:
    if xs is not None and len(xs) > MAX_LIST:
        raise ValueError(f"{name}: at most {MAX_LIST} items")
    return xs


def incident_scope(maintainer: str | None = None, advisory_id: str | None = None, campaign: str | None = None,
                   package: str | None = None, bad_version: str | None = None,
                   packages: list[str] | None = None) -> dict:
    if not (maintainer or advisory_id or campaign or (package and bad_version) or packages):
        raise ValueError("pass maintainer, advisory_id, campaign, package with bad_version, or packages")
    tr = Trace()
    return _wrap(tr, tools.incident_scope(tr, maintainer=maintainer, advisory_id=advisory_id, campaign=campaign,
                                          package=package, bad_version=bad_version,
                                          packages=_cap("packages", packages)))


def exposure(package: str | None = None, bad_version: str | None = None, depth: int = 4,
             scope: list[dict] | None = None) -> dict:
    """Same rows the API uses: one row per (service, path), with the semver decision applied.

    With `scope` (incident_scope rows: package, bad_version, clean_version, advisory_id, ...) the result is the
    agent's per-service findings instead: {exposed, safe, clean, ...} exactly as the investigation builds them."""
    if not (1 <= int(depth) <= 6):
        raise ValueError("depth must be between 1 and 6")
    tr = Trace()
    if scope is not None:
        _cap("scope", scope)
        if any(not isinstance(s, dict) or not s.get("package") or not s.get("bad_version") for s in scope):
            raise ValueError("each scope row needs package and bad_version")
        return _wrap(tr, tools.exposure(tr, scope, int(depth)))
    if not package or not bad_version:
        raise ValueError("pass package and bad_version, or scope")
    rows = tools.exposure_rows(tr, [package], depth)
    from .semver_util import check
    out = []
    for r in rows:
        ok, reason = check(bad_version, r["requirement"], r["pkg"])
        out.append(dict(r, bad_version=bad_version, exposed=bool(ok), reason=reason))
    services = sorted({r["service"] for r in out if r["exposed"]})
    return _wrap(tr, {"package": package, "bad_version": bad_version, "depth": int(depth),
                      "exposed_services": services, "rows": out})


def who_to_wake(service: str | None = None, services: list[str] | None = None) -> dict:
    svcs = list(_cap("services", services) or []) or ([service] if service else [])
    if not svcs:
        raise ValueError("pass service or services")
    tr = Trace()
    return _wrap(tr, tools.who_to_wake(tr, svcs))


def package_maintainers(package: str) -> dict:
    tr = Trace()
    return _wrap(tr, tools.package_maintainers(tr, package))


def single_points_of_failure(limit: int = 10) -> dict:
    tr = Trace()
    return _wrap(tr, tools.single_points_of_failure(tr, limit))


def path_between(service: str, package: str) -> dict:
    tr = Trace()
    return _wrap(tr, tools.path_between(tr, service, package))


def remediation_plan(trigger: str, depth: int = 4, session: str = "mcp") -> dict:
    from .router import ask
    r = ask(trigger, depth=depth, session=session, generate_fix=True)
    return {"result": {"outcome": r["outcome"], "message": r["message"], "fix": r["fix"]}, "trace": r["trace"]}


def recall(entity: str | None = None, key: str | None = None, limit: int | None = None) -> dict:
    """Prior incidents for an entity (resolved first), or for an exact entity key, or the latest `limit`."""
    tr = Trace()
    if key:
        return _wrap(tr, tools.recall(tr, key))
    if not entity:
        if limit is None:
            raise ValueError("pass entity, key or limit")
        return _wrap(tr, tools.list_incidents(tr, max(1, min(100, int(limit)))))
    ent = tools.resolve_entity(entity, tr)
    from .router import trigger_of
    t = trigger_of(ent)
    key = t[2] if t else entity
    return _wrap(tr, tools.recall(tr, key))


TOOLS = {
    "resolve_entity": resolve_entity, "incident_scope": incident_scope, "exposure": exposure,
    "who_to_wake": who_to_wake, "single_points_of_failure": single_points_of_failure,
    "path_between": path_between, "remediation_plan": remediation_plan, "recall": recall,
    "package_maintainers": package_maintainers,
}
