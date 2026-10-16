"""Regression tests for the 2026-10-16 local hang: the API froze under SSE + crew + ask load while FalkorDB was fine.

Root cause: Crew.run (async) issued its own FalkorDB writes (crew_create_run, handoffs, crew_finish) directly on
the event loop. Under load a write waits behind other writes (plus busy retries), and each wait froze every request
and stream in the process. Disconnect-and-retry clients made it worse: abandoned runs keep going by design, and
nothing capped how many ran at once.
"""
import asyncio
import os
import socket
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.streamguard import Slots

try:
    db.client().connection.ping()
    LIVE = config.GRAPH_NAME in db.client().list_graphs()
except Exception:  # noqa: BLE001
    LIVE = False
live = pytest.mark.skipif(not LIVE, reason="FalkorDB with fleet:acme not running")


@live
def test_crew_run_never_blocks_the_event_loop(monkeypatch):
    """Make every graph call slow (0.15 s); the loop must keep ticking while a crew run is in flight."""
    from app.crew.engine import Crew
    real = db.Trace.run

    def slow(self, *a, **k):
        time.sleep(0.15)
        return real(self, *a, **k)
    monkeypatch.setattr(db.Trace, "run", slow)

    async def scenario():
        gaps, stop = [], asyncio.Event()

        async def ticker():
            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        t = asyncio.create_task(ticker())
        crew = Crew("debug@4.4.2", 4, "acme", None)
        result = await crew.run()
        stop.set()
        await t
        return result, max(gaps)

    result, worst = asyncio.run(scenario())
    assert result["status"] in ("done", "no_incident")
    assert worst < 0.12, f"event loop stalled for {worst:.3f}s during a crew run"


def test_slots_global_and_per_client():
    s = Slots("t", total=3, per_key=2)
    assert s.acquire("a") is None and s.acquire("a") is None
    assert s.acquire("a") == "per_client"
    assert s.acquire("b") is None
    assert s.acquire("c") == "global"
    s.release("a")
    assert s.acquire("c") is None
    s.release("zzz")  # releasing an unknown key is a no-op, never negative
    assert s.status()["open"] == 3


def test_crew_concurrency_cap_returns_429_and_503(monkeypatch):
    from app.crew import api as crew_api
    from app.main import app
    monkeypatch.setattr(crew_api, "RUNS", Slots("crew", total=2, per_key=1))
    crew_api.limiter.reset()
    crew_api.RUNS.acquire("10.9.9.9")       # one run already in flight from this client
    crew_api.RUNS.acquire("10.8.8.8")       # and the global cap is now full
    c = TestClient(app)
    r = c.post("/api/crew/run", json={"query": "qix"}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 503 and r.json()["error"] == "crew_busy"
    crew_api.RUNS.release("10.8.8.8")
    r = c.post("/api/crew/run", json={"query": "qix"}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 429 and r.json()["scope"] == "per_client"


def test_ask_rate_limited_per_client_but_not_for_admin_or_loopback(monkeypatch):
    from app import ratelimits
    from app.byo.ratelimit import RateLimiter
    from app.main import app
    monkeypatch.setattr(ratelimits.ASK, "limit", 2)
    ratelimits.ASK.reset()
    monkeypatch.setattr(config, "ADMIN_TOKEN", "t0k-for-tests-only")
    c = TestClient(app)
    body = {"query": "", "depth": 4}  # 422 after the limiter: no graph work needed
    hdr = {"X-Forwarded-For": "203.0.113.7"}
    codes = [c.post("/api/ask", json=body, headers=hdr).status_code for _ in range(3)]
    assert codes == [422, 422, 429]
    assert c.post("/api/ask", json=body, headers={**hdr, "X-Admin-Token": "t0k-for-tests-only"}).status_code == 422
    assert isinstance(ratelimits.APPROVE, RateLimiter)


def test_tool_runner_rejects_oversized_arguments():
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/tools/exposure", json={"pkgs": ["x"] * 101})
    assert r.status_code == 400


def test_byo_fetch_refuses_other_hosts():
    import httpx

    from app.byo import sources
    with sources._client() as c:
        with pytest.raises(sources.ByoError) as e:
            c.get("https://169.254.169.254/latest/meta-data/")
        assert e.value.code == "blocked_host"
        with pytest.raises(sources.ByoError):
            c.get("http://raw.githubusercontent.com/x/y/HEAD/package.json")  # https only
    assert isinstance(sources.HTTP_TIMEOUT, httpx.Timeout)


@live
def test_watch_streams_capped_and_released_after_abrupt_disconnects():
    """Real uvicorn: per-client cap answers 429, and slots come back after clients vanish mid-stream; /health stays fast."""
    import httpx
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, WATCH_INTERVAL_S="0", AGENT_MODE="deterministic", OPENAI_API_KEY="", ADMIN_TOKEN="",
               WATCH_MAX_STREAMS_PER_IP="3", WATCH_MAX_STREAMS="5")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)

        def status():
            return httpx.get(base + "/api/watch/status", timeout=5).json()["streams"]

        for rnd in range(3):  # open to the cap, check the cap, drop everything abruptly, repeat
            socks = []
            for _ in range(3):
                sk = socket.create_connection(("127.0.0.1", port))
                sk.sendall(b"GET /api/watch/stream?tenant=acme HTTP/1.1\r\nHost: x\r\nX-Forwarded-For: 198.51.100.1\r\n\r\n")
                assert b"200 OK" in sk.recv(4096)
                socks.append(sk)
            r = httpx.get(base + "/api/watch/stream", headers={"X-Forwarded-For": "198.51.100.1"}, timeout=5)
            assert r.status_code == 429 and r.json()["scope"] == "per_client"
            t0 = time.perf_counter()
            assert httpx.get(base + "/health", timeout=5).status_code == 200
            assert time.perf_counter() - t0 < 1.0
            assert status()["open"] == 3
            for sk in socks:
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")  # RST
                sk.close()
            for _ in range(50):
                if status()["open"] == 0:
                    break
                time.sleep(0.1)
            assert status()["open"] == 0, f"round {rnd}: stream slots leaked after abrupt disconnects"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
