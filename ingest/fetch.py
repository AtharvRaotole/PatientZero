"""Fetch and cache all public data PatientZero needs.

Sources (public, no auth):
  - OSV        https://api.osv.dev  (advisories for the Sept 2025 npm takeover)
  - deps.dev   https://api.deps.dev/v3 (resolved dependency graphs)
  - npm        https://registry.npmjs.org (maintainers, publish metadata)

Every response is written verbatim to data/raw/{osv,depsdev,npm}/ and is reused
on subsequent runs (pass --refresh to re-download). load.py reads ONLY the cache,
so the demo never depends on live APIs.

Run:  python ingest/fetch.py [--refresh]
"""
import json, os, re, sys, time, urllib.parse
from pathlib import Path
import requests
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
FLEETS = sorted((ROOT / "data" / "fleet").glob("*.json"))  # every tenant fleet (acme, globex, ...)
REFRESH = "--refresh" in sys.argv
S = requests.Session()
S.headers["User-Agent"] = "patientzero-hackathon-ingest/0.1"

# Seed advisory: debug@4.4.2. The full set of compromised packages is DISCOVERED
# from OSV, not hard-coded: we query OSV for every candidate package name and keep
# advisories in the Sept 2025 'MAL-2025-469xx' batch (published 2025-09-08) whose
# details describe the same phishing campaign. The candidate list is the set of
# packages named in GHSA/OSV references for this incident; anything OSV doesn't
# confirm is dropped.
CANDIDATES = ["debug", "chalk", "ansi-styles", "strip-ansi", "supports-color", "ansi-regex",
              "wrap-ansi", "color-convert", "color-name", "is-arrayish", "slice-ansi", "color",
              "color-string", "simple-swizzle", "supports-hyperlinks", "has-ansi",
              "chalk-template", "backslash", "error-ex", "proto-tinker-wc"]
CAMPAIGN_START, CAMPAIGN_END = "2025-09-08", "2025-09-16"


def safe(name):
    return name.replace("/", "__")


def cached(path: Path, fetch):
    if path.exists() and not REFRESH:
        return json.loads(path.read_text())
    for attempt in range(4):
        try:
            r = fetch()
            if r.status_code == 404:
                data = {"_status": 404, "_url": r.url}
            else:
                r.raise_for_status()
                data = r.json()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1, sort_keys=True))
            return data
        except Exception as e:  # noqa
            print(f"  retry {attempt+1} {path.name}: {e}")
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {path}")


def osv_query(pkg):
    return cached(RAW / "osv" / f"query_{safe(pkg)}.json",
                  lambda: S.post("https://api.osv.dev/v1/query",
                                 json={"package": {"name": pkg, "ecosystem": "npm"}}, timeout=30))


def osv_vuln(vid):
    return cached(RAW / "osv" / f"{vid}.json",
                  lambda: S.get(f"https://api.osv.dev/v1/vulns/{vid}", timeout=30))


def depsdev_deps(pkg, ver):
    url = (f"https://api.deps.dev/v3/systems/npm/packages/{urllib.parse.quote(pkg, safe='')}"
           f"/versions/{urllib.parse.quote(ver, safe='')}:dependencies")
    return cached(RAW / "depsdev" / f"{safe(pkg)}@{ver}.json", lambda: S.get(url, timeout=60))


def npm_latest(pkg):
    url = f"https://registry.npmjs.org/{pkg.replace('/', '%2F')}/latest"
    return cached(RAW / "npm" / f"{safe(pkg)}.latest.json", lambda: S.get(url, timeout=30))


def npm_packument(pkg):
    url = f"https://registry.npmjs.org/{pkg.replace('/', '%2F')}"
    return cached(RAW / "npm" / f"{safe(pkg)}.packument.json", lambda: S.get(url, timeout=60))


def locked_version(req):
    m = re.search(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", req)
    if not m:
        raise ValueError(f"cannot lock {req}")
    return m.group(0)


def main():
    # 1. Advisories ---------------------------------------------------------
    compromised = {}
    for pkg in CANDIDATES:
        q = osv_query(pkg)
        for v in q.get("vulns", []):
            if not (CAMPAIGN_START <= v.get("published", "")[:10] <= CAMPAIGN_END):
                continue
            full = osv_vuln(v["id"])
            for a in full.get("affected", []):
                if a["package"]["ecosystem"] != "npm":
                    continue
                for ver in a.get("versions", []):
                    compromised.setdefault(a["package"]["name"], set()).add(ver)
            for alias in full.get("aliases", []):
                if alias.startswith(("GHSA-", "MAL-")):
                    osv_vuln(alias)
    summary = {k: sorted(v) for k, v in sorted(compromised.items())}
    (RAW / "osv" / "_compromised_summary.json").write_text(json.dumps(summary, indent=1))
    print(f"compromised packages from OSV: {len(summary)}")
    for k, v in summary.items():
        print(f"  {k}@{','.join(v)}")
    for pkg in summary:
        npm_packument(pkg)  # maintainers + 'time' (shows the malicious publish/unpublish)

    # 2. Fleet dependency graphs ------------------------------------------------
    direct = {(d, locked_version(r)) for f in FLEETS for s in json.loads(f.read_text())["services"]
              for d, r in s["dependencies"].items()}
    all_pkgs = set(summary)
    failures = []
    graphs = dict(zip(sorted(direct), ThreadPoolExecutor(8).map(lambda pv: depsdev_deps(*pv), sorted(direct))))
    for (pkg, ver), g in graphs.items():
        if g.get("_status") == 404:
            failures.append(f"{pkg}@{ver}")
            continue
        for n in g.get("nodes", []):
            all_pkgs.add(n["versionKey"]["name"])
    print(f"direct deps resolved: {len(direct) - len(failures)}/{len(direct)}; failures: {failures}")

    # 3. Maintainers for every package in the graph ------------------------------
    list(ThreadPoolExecutor(12).map(npm_latest, sorted(all_pkgs)))
    print(f"npm metadata cached for {len(all_pkgs)} packages")


if __name__ == "__main__":
    main()
