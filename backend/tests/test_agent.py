"""LLM tool-calling loop with a mocked OpenAI endpoint (no key, no network)."""
import json

import httpx
import pytest

from app import config
from app.core import agent
from app.core.registry import ToolRegistry


class FakeDomain:
    name = "fake"
    system_prompt = "sys"
    summary_prompt = "sum"

    def __init__(self):
        self.registry = ToolRegistry()
        self.calls = []

        @self.registry.register("investigate", "inv", {"type": "object", "properties": {"question": {"type": "string"}},
                                                       "required": ["question"]})
        def investigate(question, ctx=None):
            self.calls.append((question, ctx))
            return {"response": self.route(question, **(ctx or {}))}

    def route(self, text, **opts):
        return {"query": text, "outcome": "answered_with_path", "message": "m", "trace": [{"tool": "exposure"}],
                "exposed": [{"service": "img-service", "findings": [{"package": "simple-swizzle",
                                                                     "path": {"via": ["img-service", "sharp@0.33.5"]}}]}]}

    def allowed_names(self, r):
        return {"img-service", "simple-swizzle", "sharp"}

    def known_names(self):
        return {"img-service", "simple-swizzle", "sharp", "checkout-api", "lodash"}


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AGENT_MODE", "auto")


def mock_openai(monkeypatch, replies):
    it = iter(replies)

    def fake_post(url, **kw):
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"choices": [{"message": next(it)}]}, request=req)

    monkeypatch.setattr(agent.httpx, "post", fake_post)


def test_llm_picks_tool_and_answer_is_name_checked(llm_on, monkeypatch):
    d = FakeDomain()
    mock_openai(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "investigate", "arguments": json.dumps({"question": "qix"})}}]},
        {"role": "assistant", "content": "img-service is exposed via simple-swizzle, and checkout-api too."},
    ])
    r = agent.run(d, "qix", fresh=True, session="s")
    assert r["mode"] == "llm"
    assert d.calls == [("qix", {"fresh": True, "session": "s"})]  # request ctx injected, not model-controlled
    assert r["llm_unverified"] == ["checkout-api"]  # named but not in the graph result: flagged
    assert [t["tool"] for t in r["trace"]][:2] == ["llm", "exposure"]


def test_llm_error_falls_back_to_deterministic(llm_on, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(agent.httpx, "post", boom)
    r = agent.run(FakeDomain(), "qix")
    assert r["mode"] == "deterministic"
    assert "deterministic fallback" in r["trace"][0]["note"]


def test_no_key_is_deterministic(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    r = agent.run(FakeDomain(), "qix")
    assert r["mode"] == "deterministic"


def test_unknown_tool_reported_to_model(llm_on, monkeypatch):
    mock_openai(monkeypatch, [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "drop_graph", "arguments": "{}"}}]},
        {"role": "assistant", "content": "done"},
    ])
    r = agent.run(FakeDomain(), "x")
    assert any(t.get("error") and "unknown tool" in t["error"] for t in r["trace"])


def test_registry_validates_args():
    reg = FakeDomain().registry
    with pytest.raises(ValueError):
        reg.call("investigate", {})
    with pytest.raises(ValueError):
        reg.call("investigate", {"question": "q", "cypher": "MATCH (n) DELETE n"})
