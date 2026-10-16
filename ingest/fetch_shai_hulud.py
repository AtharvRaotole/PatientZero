"""Optional second incident: the Sept 2025 'Shai-Hulud' self-replicating npm worm.

Discovers the affected packages from OSV (not from memory): scans the OSV MAL-2025 id block published around
the outbreak, keeps npm records whose references name the campaign, caches every record under
data/raw/osv/shai-hulud/, and writes the curated incident file data/incidents/2025-09-15-shai-hulud.json.
load.py only loads it when INCIDENTS includes 'shai-hulud' (off by default, so fleet:acme ACs are unchanged).

Run:  python ingest/fetch_shai_hulud.py [--start 47000 --end 47700]
"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "osv" / "shai-hulud"
OUT = ROOT / "data" / "incidents" / "2025-09-15-shai-hulud.json"
MARKERS = ("shai-hulud", "shai_hulud", "shaihulud", "ctrl-tinycolor-and-40-npm-packages")
S = requests.Session()


def get(mid):
    p = RAW / f"{mid}.json"
    if p.exists():
        d = json.loads(p.read_text())
        return d if d.get("id") else None
    r = S.get(f"https://api.osv.dev/v1/vulns/{mid}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    refs = " ".join(x.get("url", "") for x in d.get("references", [])).lower()
    if any(m in refs for m in MARKERS) or any(m in (d.get("details") or "").lower() for m in MARKERS):
        RAW.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=1, sort_keys=True))
        return d
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=47000)
    ap.add_argument("--end", type=int, default=47700)
    a = ap.parse_args()
    ids = [f"MAL-2025-{n}" for n in range(a.start, a.end)]
    with ThreadPoolExecutor(16) as ex:
        recs = [r for r in ex.map(get, ids) if r]
    rows = []
    for d in sorted(recs, key=lambda d: d["id"]):
        for af in d.get("affected", []):
            if af.get("package", {}).get("ecosystem") != "npm":
                continue
            vs = sorted(af.get("versions") or [])
            if not vs:
                continue
            rows.append(dict(package=af["package"]["name"], bad_versions=vs, mal_id=d["id"],
                             aliases=sorted(d.get("aliases") or []), published=d.get("published"),
                             osv_url=f"https://osv.dev/vulnerability/{d['id']}",
                             source=next((x["url"] for x in d.get("references", [])
                                          if any(m in x.get("url", "").lower() for m in MARKERS)), None)))
    inc = dict(id="2025-09-15-shai-hulud", title="Shai-Hulud self-replicating npm worm", date="2025-09-15",
               maintainer=None,
               attack_vector="Stolen npm tokens; a postinstall payload harvests secrets and republishes the "
                             "victim maintainer's other packages",
               sources=["https://api.osv.dev/v1/vulns/{MAL id}", "https://www.wiz.io/blog/shai-hulud-npm-supply-chain-attack",
                        "https://www.stepsecurity.io/blog/ctrl-tinycolor-and-40-npm-packages-compromised"],
               notes=[f"Discovered by scanning OSV MAL-2025-{a.start}..{a.end - 1} for records whose references name "
                      "the campaign. Many maintainers, so no single HAD_PUBLISH_RIGHTS edge; no curated clean versions.",
                      "Unlike the qix incident, most packages have several bad versions."],
               compromised=rows)
    OUT.write_text(json.dumps(inc, indent=1))
    print(f"{len(recs)} OSV records, {len(rows)} packages, "
          f"{sum(len(r['bad_versions']) for r in rows)} bad versions -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
