"""Tiny helpers shared by verify.py (and usable by the backend): load named Cypher from queries/*.cypher
and apply the npm-semver exposure rule to rows returned by the `exposure` query.

File convention: each query starts with a line `// @query <name>`; other `//` lines are comments.
"""
import re
from pathlib import Path
import nodesemver

QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"


def load_queries(directory: Path = QUERIES_DIR) -> dict:
    out = {}
    for f in sorted(directory.glob("*.cypher")):
        name, buf = None, []
        for line in f.read_text().splitlines() + ["// @query __end__"]:
            m = re.match(r"\s*//\s*@query\s+(\S+)", line)
            if m:
                if name and "".join(buf).strip():
                    out[name] = "\n".join(buf).strip()
                name, buf = m.group(1), []
            elif not line.strip().startswith("//") and name:
                buf.append(line)
    return out


def admits(bad_version: str, requirement: str) -> bool:
    """npm semantics: does `requirement` (e.g. '^4.3.4', '~4.3.2', '2.6.9', '4.x') accept bad_version?"""
    try:
        return bool(nodesemver.satisfies(bad_version, requirement, loose=True))
    except Exception:
        return False


def classify(rows, bad_versions: dict):
    """rows: dicts from the `exposure` query; bad_versions: {pkg: bad_version}.
    Returns (exposed, safe) lists; each row gains `exposed` and `reason`."""
    exposed, safe = [], []
    for r in rows:
        bad = bad_versions[r["pkg"]]
        ok = admits(bad, r["requirement"])
        r = dict(r, bad_version=bad, exposed=ok,
                 reason=(f"{r['pkg']} {r['requirement']} admits {bad}" if ok else
                         f"{r['pkg']} {r['requirement']} does not satisfy {bad}"))
        (exposed if ok else safe).append(r)
    return exposed, safe
