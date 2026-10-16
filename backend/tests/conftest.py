"""The deterministic router is the test oracle: tests never call the real LLM API (and never need a key)."""
import pytest

from app import config
from app.core import agent

config.AGENT_MODE = "deterministic"
config.LLM_CACHE_FILE = ""  # no disk cache in tests
config.LLM_USAGE_DIR = ""   # no usage log / budget file in tests

# Rate-limit tests name their client with X-Forwarded-For, as if one trusted proxy (our own) sat in front of the
# app. Production on Railway uses X-Real-IP instead (see app/byo/ratelimit.py); test_client_ip.py covers both.
from app.byo import ratelimit as _rl  # noqa: E402
_rl.TRUSTED_PROXY_HOPS = 1


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    agent._cache.clear()
    agent.budget.reset_for_tests()
    yield
    agent._cache.clear()


@pytest.fixture(autouse=True, scope="session")
def _no_leftover_graphs():
    """Tests clean up after themselves: BYO scan graphs created during the run are deleted at the end."""
    from app import db
    try:
        before = set(db.client().list_graphs())
    except Exception:  # noqa: BLE001 - FalkorDB down: live tests are skipped anyway
        yield
        return
    yield
    for g in set(db.client().list_graphs()) - before:
        if g.startswith("byo:"):
            db.client().select_graph(g).delete()
