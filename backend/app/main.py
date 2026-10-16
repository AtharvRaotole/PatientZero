"""PatientZero backend (FastAPI + FalkorDB)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Load repo-root .env first, then backend/.env (if present).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv()

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
CORS_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000"
).split(",") if o.strip()]

app = FastAPI(title="PatientZero API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_db: FalkorDB | None = None


def get_db() -> FalkorDB:
    global _db
    if _db is None:
        _db = FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT)
    return _db


@app.get("/health")
def health():
    try:
        db = get_db()
        db.connection.ping()
        return {"status": "ok", "falkordb": "up", "host": FALKORDB_HOST, "port": FALKORDB_PORT,
                "graphs": db.list_graphs()}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "falkordb": "down", "error": str(e)}


@app.get("/api/graph/demo")
def graph_demo():
    """Seed a tiny sample graph (idempotent) and return nodes/edges for visualisation."""
    try:
        g = get_db().select_graph("demo")
        g.query(
            "MERGE (a:Person {name:'Alice'}) MERGE (b:Person {name:'Bob'}) "
            "MERGE (c:Place {name:'Cafe'}) "
            "MERGE (a)-[:VISITED]->(c) MERGE (b)-[:VISITED]->(c) MERGE (a)-[:KNOWS]->(b)"
        )
        res = g.query(
            "MATCH (s)-[r]->(t) RETURN id(s), labels(s)[0], s.name, type(r), id(t), labels(t)[0], t.name"
        )
        nodes, edges = {}, []
        for sid, sl, sn, rt, tid, tl, tn in res.result_set:
            nodes[sid] = {"id": sid, "label": sl, "name": sn}
            nodes[tid] = {"id": tid, "label": tl, "name": tn}
            edges.append({"source": sid, "target": tid, "type": rt})
        return {"graph": "demo", "nodes": list(nodes.values()), "edges": edges}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"FalkorDB error: {e}")
