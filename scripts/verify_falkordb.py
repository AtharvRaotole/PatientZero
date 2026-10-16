"""Smoke test: create a tiny graph, query it with Cypher, print results, clean up."""
import os
from falkordb import FalkorDB

db = FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"), port=int(os.getenv("FALKORDB_PORT", "6379")))
g = db.select_graph("smoke_test")
g.query("CREATE (:Patient {id:'P0'})-[:CONTACT {day:1}]->(:Patient {id:'P1'})-[:CONTACT {day:2}]->(:Patient {id:'P2'})")
res = g.query("MATCH p=(a:Patient {id:'P0'})-[:CONTACT*1..3]->(b) RETURN b.id, length(p) ORDER BY length(p)")
for row in res.result_set:
    print("reachable:", row)
g.delete()
print("OK: FalkorDB Cypher smoke test passed")
