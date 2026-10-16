"""npm semver semantics for the exposure decision (last-edge requirement vs bad version)."""
from __future__ import annotations

import nodesemver


def check(bad_version: str, requirement: str | None, pkg: str = "") -> tuple[bool | None, str]:
    """Return (exposed, reason). exposed=None means the range could not be evaluated."""
    if requirement is None or str(requirement).strip() == "":
        return None, "no requirement recorded on the edge"
    req = str(requirement).strip()
    if req.startswith(("npm:", "git", "http", "file:", "link:", "workspace:")) or "/" in req:
        return None, f"requirement '{req}' is not a semver range"
    try:
        ok = nodesemver.satisfies(bad_version, req, loose=True)
    except Exception as e:  # noqa: BLE001
        return None, f"could not parse range '{req}': {e}"
    kind = describe(req)
    if ok:
        return True, f"{pkg} {req}{kind} admits {bad_version}".strip()
    return False, f"{pkg} {req}{kind} does not satisfy {bad_version}".strip()


def describe(req: str) -> str:
    r = req.strip()
    if r.startswith("^"):
        return " (caret)"
    if r.startswith("~"):
        return " (tilde)"
    if nodesemver.valid(r, loose=True):
        return " (exact)"
    return ""
