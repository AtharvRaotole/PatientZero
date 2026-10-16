"""Load the demo graph only if it is not there yet (safe to run on every container start).

Never wipes an existing graph, so incident and playbook memory survive redeploys. Waits for FalkorDB first.
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
            return 0
    print(f"seed: graph '{load.GRAPH}' missing, loading from data/ (no network)")
    load.load(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
