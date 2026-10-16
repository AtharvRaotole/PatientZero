"""P1-1: any service, package or person the LLM names that is not in the tool result is flagged, and the answer
falls back to the templated deterministic sentence. Mocked OpenAI only (no key, no network)."""
import json

import httpx
import pytest

from app import config, db
from app.core import agent
from app.core.agent import unverified_names
from app.domains.patientzero import DOMAIN

ALLOWED = {"img-service", "simple-swizzle", "sharp", "Sofia Alvarez", "@sofia", "#team-storefront"}
KNOWN = {"img-service", "simple-swizzle", "sharp", "checkout-api"}


@pytest.mark.parametrize("text,bad", [
    ("payments-api and billing-service are exposed via left-pad.", ["payments-api", "billing-service", "left-pad"]),
    ("Services billing-service and payments-api are exposed via left-pad; page Priya Raman.",
     ["Priya Raman", "billing-service", "payments-api", "left-pad"]),
    ("img-service is exposed; also checkout-api.", ["checkout-api"]),
    ("Ping @mallory about img-service.", ["@mallory"]),
    ("img-service reaches simple-swizzle@0.2.3 through sharp. Page Sofia Alvarez (@sofia) in #team-storefront, "
     "then re-run the on-call tier-1 check on 2025-09-08.", []),
])
def test_unverified_names(text, bad):
    assert unverified_names(text, ALLOWED, KNOWN) == bad


ping = db.ping()
live = pytest.mark.skipif(not (ping["ok"] and ping.get("graph_loaded")), reason="FalkorDB fleet:acme not available")


def mock_openai(monkeypatch, replies):
    it = iter(replies)
    sent = []

    def fake_post(url, **kw):
        sent.append(kw["json"])
        return httpx.Response(200, json={"choices": [{"message": next(it)}], "usage": {"total_tokens": 10}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(agent.httpx, "post", fake_post)
    return sent


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AGENT_MODE", "auto")


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


@live
def test_invented_service_and_person_fall_back_to_template(llm_on, monkeypatch):
    q = "Is payments-gateway exposed to the left-pad compromise?"
    mock_openai(monkeypatch, [call("investigate", {"question": q}),
                              {"role": "assistant", "content": "billing-gateway is not exposed to left-pad. "
                                                               "Wake Mallory Quinn (@mallory) just in case."}])
    r = agent.run(DOMAIN, q, mode="llm", fresh=True)
    assert r["llm_summary"] is None and r.get("llm_rejected") is True
    assert {"billing-gateway", "left-pad", "Mallory Quinn", "@mallory"} <= set(r["llm_unverified"])
    assert r["outcome"] == "unknown_entity"  # the templated deterministic answer for the same question
    # payments-gateway is a real fleet service; left-pad is not in the graph, and the template says so
    assert "left-pad is not" in r["message"] and r["exposed_count"] == 0
    assert any(s.get("llm_rejected") for s in r["trace"])


@live
def test_grounded_llm_answer_is_kept_and_prompt_is_small(llm_on, monkeypatch):
    q = "are we safe from that npm phishing attack last september?"
    sent = mock_openai(monkeypatch, [{"role": "assistant", "content": "13 of 22 services are exposed, including "
                                                                      "img-service via simple-swizzle."}])
    r = agent.run(DOMAIN, q, mode="llm", fresh=True)
    assert r["llm_summary"] and r["llm_unverified"] == [] and r["exposed_count"] == 13
    assert len(sent) == 1  # the router pre-ran investigate, so the model only phrases the answer
    # Whole second request (system + tools + question + tool result) stays well under 2k tokens (~4 chars/token).
    assert len(json.dumps(sent[-1])) < 8000


@live
def test_natural_phrasing_routes_to_qix_without_llm():
    for q in ["the guy who maintains chalk got phished", "are we safe from that npm phishing attack last september?"]:
        r = agent.run(DOMAIN, q, fresh=True)
        assert r["mode"] == "deterministic" and r["entity_key"] == "qix" and r["exposed_count"] == 13
