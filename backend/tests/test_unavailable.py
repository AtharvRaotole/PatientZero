"""AC14 without killing the shared redis: point the client at a closed port and expect a clean 503."""
from fastapi.testclient import TestClient

from app import config, db
from app.main import app


def test_graph_unavailable_returns_503(monkeypatch):
    monkeypatch.setattr(config, "FALKORDB_PORT", 1)  # nothing listens there
    monkeypatch.setattr(db, "_db", None)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/ask", json={"query": "qix"})
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "database_unavailable"
        assert "unavailable" in body["message"] and "Error" not in body["message"] and "111" not in body["message"]
        h = c.get("/health").json()
        assert h["falkordb"] == "down"
    finally:
        db._db = None
