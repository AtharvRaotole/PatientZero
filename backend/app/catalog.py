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
}


def _wrap(tr: Trace, result: Any) -> dict:
    return {"result": result, "trace": tr.steps}


def resolve_entity(text: str) -> dict:
    tr = Trace()
    return _wrap(tr, tools.resolve_entity(text, tr))


def incident_scope(maintainer: str | None = None, advisory_id: str | None = None) -> dict:
    if not maintainer and not advisory_id:
        raise ValueError("pass maintainer or advisory_id")
    tr = Trace()
    return _wrap(tr, tools.incident_scope(tr, maintainer=maintainer, advisory_id=advisory_id))


def exposure(package: str, bad_version: str, depth: int = 4) -> dict:
    """Same rows the API uses: one row per (service, path), with the semver decision applied."""
    if not (1 <= int(depth) <= 6):
        raise ValueError("depth must be between 1 and 6")
    tr = Trace()
    rows = tools.exposure_rows(tr, [package], depth)
    from .semver_util import check
    out = []
    for r in rows:
        ok, reason = check(bad_version, r["requirement"], r["pkg"])
        out.append(dict(r, bad_version=bad_version, exposed=bool(ok), reason=reason))
    services = sorted({r["service"] for r in out if r["exposed"]})
    return _wrap(tr, {"package": package, "bad_version": bad_version, "depth": int(depth),
                      "exposed_services": services, "rows": out})


def who_to_wake(service: str) -> dict:
    tr = Trace()
    return _wrap(tr, tools.who_to_wake(tr, [service]))


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


def recall(entity: str) -> dict:
    tr = Trace()
    ent = tools.resolve_entity(entity, tr)
    from .router import trigger_of
    t = trigger_of(ent)
    key = t[2] if t else entity
    return _wrap(tr, tools.recall(tr, key))


TOOLS = {
    "resolve_entity": resolve_entity, "incident_scope": incident_scope, "exposure": exposure,
    "who_to_wake": who_to_wake, "single_points_of_failure": single_points_of_failure,
    "path_between": path_between, "remediation_plan": remediation_plan, "recall": recall,
}
