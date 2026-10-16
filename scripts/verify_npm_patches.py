"""Install-check the generated package.json patches with real npm (P1-2).

For each target: original dependencies + PatientZero patch -> temp dir -> `npm install --ignore-scripts`, then
`npm ls --all --json` must contain none of the 19 malicious versions, and every compromised package must resolve
to a version >= its clean release on the compromised major. Also checks chalk ^4 still works with the fleet-wide
overrides. Run from backend/:  uv run python ../scripts/verify_npm_patches.py [--npm "npx -y npm@10"]
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db, router, tools  # noqa: E402
from app.byo import scan  # noqa: E402

INC = json.loads((ROOT / "data/incidents/2025-09-08-qix.json").read_text())
BAD = {(c["package"], c["bad_version"]) for c in INC["compromised"]}
CLEAN = {c["package"]: c["clean_version"] for c in INC["compromised"]}


def walk(tree: dict, out: list):
    for name, node in (tree.get("dependencies") or {}).items():
        if node.get("version"):
            out.append((name, node["version"]))
        walk(node, out)


def install(name: str, pkg: dict, npm: list[str], check: str | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="pz-npm-") as d:
        (Path(d) / "package.json").write_text(json.dumps(pkg, indent=2))
        r = subprocess.run(npm + ["install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"],
                           cwd=d, capture_output=True, text=True, timeout=900)
        res = {"target": name, "install_rc": r.returncode, "error": (r.stderr or "")[-600:] if r.returncode else ""}
        if r.returncode:
            return res
        ls = subprocess.run(npm + ["ls", "--all", "--json"], cwd=d, capture_output=True, text=True, timeout=300)
        got: list = []
        walk(json.loads(ls.stdout or "{}"), got)
        res["malicious_present"] = sorted({f"{p}@{v}" for p, v in got if (p, v) in BAD})
        res["resolved"] = {p: sorted({v for q, v in got if q == p}) for p in CLEAN if any(q == p for q, _ in got)}
        if check:
            c = subprocess.run(["node", "-e", check], cwd=d, capture_output=True, text=True)
            res["runtime_check"] = (c.stdout.strip() or c.stderr.strip()[-300:])
            res["runtime_ok"] = c.returncode == 0
        return res


def fleet_targets() -> list[tuple[str, dict]]:
    acme = json.loads((ROOT / "data/fleet/acme.json").read_text())
    by = {s["name"]: s for s in acme["services"]}
    r = router.ask("maintainer qix was phished", fresh=True)
    fix = tools.build_fix(r, r["wake"])
    out = []
    for p in fix["per_service"]:
        if p["service"] in ("shipping-tracker", "auth-service", "ci-runner"):
            deps = dict(by[p["service"]]["dependencies"])
            deps.update(p["package_json"].get("dependencies", {}))
            out.append((f"fleet:{p['service']}", {"name": p["service"], "private": True, "dependencies": deps,
                                                  "overrides": p["package_json"]["overrides"]}))
    out.append(("fleet-wide overrides + chalk ^4", {"name": "chalk4", "private": True,
                                                    "dependencies": {"chalk": "^4.1.2", "strip-ansi": "^6.0.1"},
                                                    "overrides": fix["combined"]["overrides"]}))
    return out


def byo_target(cache: str, url: str) -> tuple[str, dict]:
    orig = json.loads((ROOT / f"data/raw/byo/github/{cache}/package.json").read_text())
    d = scan.run_scan(github_url=url, include_dev=True)
    try:
        patch = d["overrides"]
        pkg = {"name": "byo-check", "private": True,
               "dependencies": {**orig.get("dependencies", {}), **patch.get("dependencies", {})},
               "devDependencies": {**orig.get("devDependencies", {}), **patch.get("devDependencies", {})},
               "overrides": patch.get("overrides", {})}
        return f"byo:{url}", pkg
    finally:
        db.client().select_graph(d["graph_name"]).delete()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npm", default="npx -y npm@10")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    npm = shlex.split(a.npm)
    print("npm", subprocess.run(npm + ["-v"], capture_output=True, text=True).stdout.strip(),
          "node", subprocess.run(["node", "-v"], capture_output=True, text=True).stdout.strip())
    targets = fleet_targets() + [byo_target("expressjs__express__HEAD", "https://github.com/expressjs/express"),
                                 byo_target("webpack__webpack__HEAD", "https://github.com/webpack/webpack")]
    ok = True
    for name, pkg in targets:
        if a.only and a.only not in name:
            continue
        check = "const c=require('chalk');console.log('chalk.red ok:',typeof c.red('x'))" if "chalk" in name else None
        print(f"\n== {name}\npatch overrides: {json.dumps(pkg.get('overrides'))}", flush=True)
        r = install(name, pkg, npm, check)
        print(json.dumps(r, indent=1), flush=True)
        ok &= r["install_rc"] == 0 and not r.get("malicious_present") and r.get("runtime_ok", True)
    print("\nALL OK" if ok else "\nFAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
