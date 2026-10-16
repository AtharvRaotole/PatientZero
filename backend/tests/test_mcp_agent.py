"""The agent selects and runs its tools through PatientZero's own MCP server (in-memory transport).

Needs FalkorDB with fleet:acme (like test_integration). The LLM is mocked: no key, no network.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.core import agent
from app.domains.patientzero import DOMAIN
from app.main import AGENT_DOMAIN, app
from app.mcp_agent import client as mcp_client
from app.mcp_agent import domain as mcp_domain
from app.mcp_agent.domain import McpDomain, compact_schema

try:
    LIVE = config.GRAPH_NAME in db.client().list_graphs()
except Exception:  # noqa: BLE001
    LIVE = False
pytestmark = pytest.mark.skipif(not LIVE, reason="FalkorDB with fleet:acme not running")

c = TestClient(app)


def mcp_steps(r):
    return [s for s in r["trace"] if s.get("tool") == "mcp"]


def test_agent_domain_is_mcp_backed():
    assert isinstance(AGENT_DOMAIN, McpDomain)
    h = c.get("/health").json()
    assert h["mcp_agent"]["active"] is True and h["mcp_agent"]["transport"] == "memory"


def test_mcp_server_lists_spec_tools_plus_investigate():
    tools, _, _ = mcp_client.list_tools(refresh=True)
    names = {t["name"] for t in tools}
    assert names >= {"resolve_entity", "incident_scope", "exposure", "who_to_wake", "single_points_of_failure",
                     "path_between", "remediation_plan", "recall", "investigate"}


@pytest.mark.parametrize("q,intent", [
    ("maintainer qix was phished", "exposure"),
    ("who to wake for auth-service", "who_to_wake"),
    ("path from img-service to simple-swizzle", "path_between"),
    ("single points of failure", "single_points_of_failure"),
])
def test_ask_runs_through_mcp_and_matches_direct(q, intent):
    r = c.post("/api/ask", json={"query": q, "mode": "deterministic"}).json()
    assert r["tool_via"] == "mcp"
    st = mcp_steps(r)[0]
    assert st["via"] == "mcp" and st["mcp_tool"] == "investigate" and st["intent"] == intent
    assert st["params"]["question"] == q and st["ms"] > 0 and st["tools_listed"] >= 9
    assert r["mcp"]["transport"] == "memory"
    direct = DOMAIN.route(q)
    assert r["outcome"] == direct["outcome"] and r["exposed_count"] == direct["exposed_count"]
    assert [e["service"] for e in r["exposed"]] == [e["service"] for e in direct["exposed"]]
    assert r["intent"] == direct["intent"]


def test_depth_and_session_are_carried_as_tool_args():
    r = c.post("/api/ask", json={"query": "debug@4.4.2", "depth": 2, "session": "s-mcp", "mode": "deterministic"}).json()
    st = mcp_steps(r)[0]
    assert st["params"]["depth"] == 2 and st["params"]["session"] == "s-mcp"
    assert r["depth"] == 2


def test_transport_failure_falls_back_to_direct_with_flag(monkeypatch):
    def broken(*a, **k):
        raise ConnectionError("simulated transport failure")
    monkeypatch.setattr(mcp_client, "call_tool", broken)
    r = c.post("/api/ask", json={"query": "who to wake for auth-service", "mode": "deterministic"}).json()
    assert r["tool_via"] == "direct" and r["mcp"]["via"] == "direct"
    st = mcp_steps(r)[0]
    assert st["via"] == "direct" and "ConnectionError" in st["mcp_fallback"]
    assert r["outcome"] == "answered_with_path" and r["wake"]


def test_unknown_route_option_is_never_dropped():
    r = AGENT_DOMAIN.route("who to wake for auth-service", session="x", tenant_hint="t1")
    assert r["tool_via"] == "mcp"  # unknown options travel in `options`, filtered server side to what ask accepts
    assert mcp_steps(r)[0]["params"]["options"] == {"tenant_hint": "t1"}


def test_compact_schema_hides_context_args():
    tools, _, _ = mcp_client.list_tools()
    inv = next(t for t in tools if t["name"] == "investigate")
    s = compact_schema(inv["inputSchema"], hide=("fresh", "session", "generate_fix", "options"))
    assert set(s["properties"]) == {"question", "depth"} and s["required"] == ["question"]
    assert s["properties"]["depth"] == {"type": "integer", "minimum": 1, "maximum": 6}
    assert "title" not in json.dumps(s)


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AGENT_MODE", "auto")


def test_llm_path_gets_mcp_tool_schemas_and_calls_through_mcp(llm_on, monkeypatch):
    seen = {}
    replies = iter([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "who_to_wake", "arguments": json.dumps({"service": "auth-service"})}}]},
        {"role": "assistant", "content": "Wake Amara Okafor."},
    ])

    def fake_post(url, **kw):
        seen.setdefault("tools", kw["json"].get("tools"))
        return httpx.Response(200, json={"choices": [{"message": next(replies)}], "usage": {"total_tokens": 10}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(agent.httpx, "post", fake_post)
    r = agent.run(AGENT_DOMAIN, "please help with something odd", mode="llm", session="s")
    names = {t["function"]["name"] for t in seen["tools"]}
    assert "remediation_plan" not in names and {"investigate", "who_to_wake", "exposure"} <= names
    inv = next(t for t in seen["tools"] if t["function"]["name"] == "investigate")
    assert set(inv["function"]["parameters"]["properties"]) == {"question", "depth"}
    st = [s for s in mcp_steps(r) if s.get("mcp_tool") == "who_to_wake"]
    assert st and st[0]["via"] == "mcp" and st[0]["params"] == {"service": "auth-service"}
    assert any(s.get("query") == "who_to_wake" for s in r["trace"])  # the tool's own Cypher step came back too


def test_llm_cannot_call_write_tool():
    with pytest.raises(ValueError):
        AGENT_DOMAIN.registry.call("remediation_plan", {"trigger": "qix"})


def test_disabled_flag_returns_plain_domain(monkeypatch):
    monkeypatch.setattr(mcp_domain, "ENABLED", False)
    assert mcp_domain.wrap_domain(DOMAIN) is DOMAIN


def test_stdio_transport_lists_tools():
    tools, ms, cached = mcp_client.list_tools(transport="stdio", refresh=True)
    assert "investigate" in {t["name"] for t in tools} and not cached


def test_fix_runs_through_mcp_investigate_with_generate_fix():
    r = c.post("/api/fix", json={"query": "chalk@5.6.1", "depth": 4, "session": "t-mcp-fix"}).json()
    st = mcp_steps(r)[0]
    assert st["via"] == "mcp" and st["params"].get("generate_fix") is True
    assert r["fix"] and r["fix"].get("incident_id")


def test_tenant_context_reaches_the_mcp_tool():
    tenants = pytest.importorskip("app.tenants")
    if "globex" not in tenants.TENANTS or tenants.TENANTS["globex"] not in db.client().list_graphs():
        pytest.skip("globex tenant not loaded")
    with tenants.use("globex"):
        via_mcp = AGENT_DOMAIN.route("maintainer qix was phished")
        direct = DOMAIN.route("maintainer qix was phished")
    with tenants.use("acme"):
        acme = DOMAIN.route("maintainer qix was phished")
    assert via_mcp["tool_via"] == "mcp"
    assert [e["service"] for e in via_mcp["exposed"]] == [e["service"] for e in direct["exposed"]]
    assert {e["service"] for e in via_mcp["exposed"]} != {e["service"] for e in acme["exposed"]}


def test_write_tools_hidden_from_llm():
    assert {"remediation_plan", "crew_run"} <= mcp_domain.LLM_HIDDEN_TOOLS
    names = {t["function"]["name"] for t in AGENT_DOMAIN.registry.openai_tools()}
    assert "crew_run" not in names and "remediation_plan" not in names
    with pytest.raises(ValueError):
        AGENT_DOMAIN.registry.call("crew_run", {})
