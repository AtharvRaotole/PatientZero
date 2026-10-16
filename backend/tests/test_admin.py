"""P2-4: destructive endpoints need X-Admin-Token when ADMIN_TOKEN is set."""
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

client = TestClient(app)
ping = db.ping()
live = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")), reason="FalkorDB fleet:acme not available")
GUARDED = [("post", "/api/memory/reset"), ("delete", "/api/playbooks"), ("post", "/api/byo/cleanup"),
           ("post", "/api/admin/reset")]


@pytest.mark.parametrize("method,path", GUARDED)
def test_guarded_without_or_with_wrong_token(monkeypatch, method, path):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret-test")
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"X-Admin-Token": "nope"}).status_code == 401


@live
def test_correct_token_passes(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret-test")
    r = client.post("/api/byo/cleanup", headers={"X-Admin-Token": "s3cret-test"})
    assert r.status_code == 200
    assert client.get("/health").json()["admin_guard"] is True
