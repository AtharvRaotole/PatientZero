"""Parity: the MCP tool chain returns exactly the same answer as the plain router (MCP_AGENT=off).

Every README "Try it yourself" input, the "What you can do" questions, follow-ups in a session, both tenants and
the fix flow are asked twice through /api/ask: once with the agent's domain swapped for the plain domain (what
MCP_AGENT=off serves), once through the MCP chain. Everything but the trace, timings and the mcp metadata must
be identical, and the MCP trace must start with an MCP tools/call step.
Needs FalkorDB with fleet:acme (like test_integration). No LLM.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, db, main
from app.domains.patientzero import DOMAIN
from app.mcp_agent.domain import INTENT_TOOL

try:
    LIVE = config.GRAPH_NAME in db.client().list_graphs()
except Exception:  # noqa: BLE001
    LIVE = False
pytestmark = pytest.mark.skipif(not LIVE, reason="FalkorDB with fleet:acme not running")

c = TestClient(main.app)

README = ["qix", "debug@4.4.2", "chalk@5.6.1", "GHSA-4x49-vf9v-38px", "Shai-Hulud", "who to wake for auth-service",
          "path from img-service to simple-swizzle", "single points of failure"]
MORE = ["qix was phished, who is affected?", "maintainer qix was phished", "who do I wake for checkout-api",
        "where are our single points of failure?", "why is img-service connected to is-arrayish?",
        "the guy who maintains chalk got phished", "were we hit by the Shai-Hulud worm?",
        "who maintains chalk", "show past incidents", "left-pad@9.9.9", "GHSA-zzzz-zzzz-zzzz", "hello there",
        "is checkout-api exposed to left-pad", "debug@4.4.2 was hacked, who do I wake"]
FOLLOWUPS = [("maintainer qix was phished", None), ("who should I wake for that?", None), ("and at depth 6?", None),
             ("debug@4.4.2", 2)]
VOLATILE = {"trace", "total_ms", "mcp", "tool_via", "session", "mode"}


def norm(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in VOLATILE}


def ask(q, *, mcp: bool, monkeypatch, **kw):
    monkeypatch.setattr(main, "AGENT_DOMAIN", main.AGENT_DOMAIN if mcp else DOMAIN)
    r = c.post("/api/ask", json={"query": q, "mode": "deterministic", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def both(q, monkeypatch, **kw):
    real = main.AGENT_DOMAIN
    off = ask(q, mcp=False, monkeypatch=monkeypatch, **kw)
    monkeypatch.setattr(main, "AGENT_DOMAIN", real)
    on = ask(q, mcp=True, monkeypatch=monkeypatch, **kw)
    return off, on


def check_mcp_trace(r):
    assert r["tool_via"] == "mcp", r.get("mcp")
    steps = [s for s in r["trace"] if s.get("tool") != "agent"]
    assert steps[0].get("via") == "mcp" and steps[0]["tool"] == "mcp", steps[0]
    assert r["mcp"]["tools"] and r["mcp"]["tools"][0] == "resolve_entity"
    assert "investigate" not in r["mcp"]["tools"]
    assert r["mcp"]["tool"] == INTENT_TOOL[r["intent"]]
    mcp_steps = [s for s in r["trace"] if s.get("tool") == "mcp"]
    assert [s["mcp_tool"] for s in mcp_steps] == r["mcp"]["tools"]
    assert all(s["via"] == "mcp" for s in mcp_steps)


@pytest.mark.parametrize("q", README + MORE)
def test_single_question_parity(q, monkeypatch):
    off, on = both(q, monkeypatch)
    assert norm(on) == norm(off)
    check_mcp_trace(on)


@pytest.mark.parametrize("depth", [1, 2, 6])
def test_depth_parity(depth, monkeypatch):
    off, on = both("debug@4.4.2", monkeypatch, depth=depth)
    assert norm(on) == norm(off) and on["depth"] == depth


def test_followup_session_parity(monkeypatch):
    sid_off, sid_on = f"par-off-{uuid.uuid4().hex[:8]}", f"par-on-{uuid.uuid4().hex[:8]}"
    real = main.AGENT_DOMAIN
    for q, depth in FOLLOWUPS:
        kw = {"depth": depth} if depth else {}
        off = ask(q, mcp=False, monkeypatch=monkeypatch, session_id=sid_off, **kw)
        monkeypatch.setattr(main, "AGENT_DOMAIN", real)
        on = ask(q, mcp=True, monkeypatch=monkeypatch, session_id=sid_on, **kw)
        assert norm(on) == norm(off), q
        assert on["tool_via"] == "mcp"


def test_globex_tenant_parity(monkeypatch):
    tenants = pytest.importorskip("app.tenants")
    if "globex" not in tenants.TENANTS or tenants.TENANTS["globex"] not in db.client().list_graphs():
        pytest.skip("globex tenant not loaded")
    for q in ("Shai-Hulud", "maintainer qix was phished", "single points of failure"):
        off, on = both(q, monkeypatch, tenant="globex")
        assert norm(on) == norm(off), q
        check_mcp_trace(on)
