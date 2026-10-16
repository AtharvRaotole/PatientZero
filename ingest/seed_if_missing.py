"""Load the demo graph only if it is not there yet (safe to run on every container start).

Never wipes an existing graph, so incident and playbook memory survive redeploys. Waits for FalkorDB first.
With INCIDENTS including shai-hulud, an existing graph gets the campaign advisories added idempotently.
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


def ensure_incidents(g) -> None:
    """Optional incidents (INCIDENTS=qix,shai-hulud) added to an EXISTING graph with MERGE only, so a redeploy
    can switch them on without wiping incident or playbook memory. Never fails the start."""
    if "shai-hulud" not in load.INCIDENTS:
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
    if load.GRAPH in graphs:
        n = db.select_graph(load.GRAPH).ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0]
        if n:
            print(f"seed: graph '{load.GRAPH}' already loaded ({n} services); leaving it (and its memory) alone")
            ensure_incidents(db.select_graph(load.GRAPH))
            return 0
    print(f"seed: graph '{load.GRAPH}' missing, loading from data/ (no network)")
    load.load(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
