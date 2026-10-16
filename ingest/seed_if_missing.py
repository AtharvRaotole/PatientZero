"""Load the demo graph only if it is not there yet (safe to run on every container start).

Never wipes an existing graph, so incident and playbook memory survive redeploys. Waits for FalkorDB first.
With INCIDENTS including shai-hulud, an existing graph gets the campaign advisories added idempotently.
Every tenant in TENANTS (default 'acme,globex') gets its own graph fleet:<tenant>, seeded the same way.
Run:  python ingest/seed_if_missing.py      (same FALKORDB_* env as load.py; FALKORDB_URL also accepted)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from falkordb import FalkorDB  # noqa: E402

import load  # noqa: E402


def connect():
    url = os.getenv("FALKORDB_URL")
    return FalkorDB.from_url(url) if url else load.connect()


def ensure_qix_campaign(g) -> None:
    """Graphs loaded before advisories carried a campaign tag have the 19 qix advisories untagged, so the timeline
    lists them one by one instead of as one campaign event (and campaign scope misses them). Tag them like a fresh
    load does: only advisories on packages the qix maintainer had publish rights to. Idempotent, never fails."""
    try:
        n = g.query("MATCH (:Maintainer)-[h:HAD_PUBLISH_RIGHTS]->(:Package)-[:HAS_VERSION]->(:Version)<-[:AFFECTS]-(a:Advisory) "
                    "WHERE a.campaign IS NULL AND h.incident IS NOT NULL SET a.campaign = h.incident "
                    "RETURN count(DISTINCT a)").result_set[0][0]
        if n:
            print(f"seed: tagged {n} untagged incident advisories with their campaign")
    except Exception as e:  # noqa: BLE001
        print(f"seed: campaign backfill skipped: {e}", file=sys.stderr)


def ensure_incidents(g, tenant: str = load.TENANT) -> None:
    """Optional incidents (INCIDENTS=qix,shai-hulud) added to an EXISTING graph with MERGE only, so a redeploy
    can switch them on without wiping incident or playbook memory. Never fails the start."""
    if "shai-hulud" not in load.incidents_for(tenant):
        return
    try:
        n = g.ro_query("MATCH (a:Advisory {campaign:'2025-09-15-shai-hulud'}) RETURN count(a)").result_set[0][0]
        if n:
            print(f"seed: shai-hulud already loaded ({n} advisories)")
            return
        print("seed: adding shai-hulud advisories to the existing graph (MERGE, memory untouched)")
        load.load_shai_hulud(g)
    except Exception as e:  # noqa: BLE001
        print(f"seed: shai-hulud ensure step failed, continuing without it: {e}", file=sys.stderr)


def main(wait_s: int = 90) -> int:
    deadline = time.time() + wait_s
    while True:
        try:
            db = connect()
            graphs = db.list_graphs()
            break
        except Exception as e:  # noqa: BLE001
            if time.time() > deadline:
                print(f"seed: FalkorDB not reachable after {wait_s}s: {e}", file=sys.stderr)
                return 1
            print(f"seed: waiting for FalkorDB ({e})")
            time.sleep(3)
    for tenant in tenants():
        graph = load.GRAPH if tenant == load.TENANT else f"fleet:{tenant}"
        if graph in graphs:
            n = db.select_graph(graph).ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0]
            if n:
                print(f"seed: graph '{graph}' already loaded ({n} services); leaving it (and its memory) alone")
                ensure_qix_campaign(db.select_graph(graph))
                ensure_incidents(db.select_graph(graph), tenant)
                continue
        if not load.fleet_file(tenant).exists():
            print(f"seed: no fleet file for tenant '{tenant}', skipping", file=sys.stderr)
            continue
        print(f"seed: graph '{graph}' missing, loading tenant '{tenant}' from data/ (no network)")
        load.load(db, tenant=tenant, graph=graph)
    return 0


def tenants() -> list[str]:
    """TENANTS env as used by the API: 'acme,globex' or 'acme=fleet:acme,...' (graph names are fleet:<tenant>)."""
    out = []
    for part in (os.getenv("TENANTS") or "acme,globex").split(","):
        name = part.strip().lower().partition("=")[0]
        if name and name not in out:
            out.append(name)
    if load.TENANT not in out:
        out.insert(0, load.TENANT)
    return out


if __name__ == "__main__":
    sys.exit(main())
