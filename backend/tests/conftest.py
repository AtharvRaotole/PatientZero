"""The deterministic router is the test oracle: tests never call the real LLM API (and never need a key)."""
import pytest

from app import config
from app.core import agent

config.AGENT_MODE = "deterministic"
config.LLM_CACHE_FILE = ""  # no disk cache in tests


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    agent._cache.clear()
    yield
    agent._cache.clear()
