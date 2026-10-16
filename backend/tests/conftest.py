"""The deterministic router is the test oracle: tests never call the real LLM API (and never need a key)."""
import pytest

from app import config
from app.core import agent

config.AGENT_MODE = "deterministic"
config.LLM_CACHE_FILE = ""  # no disk cache in tests
config.LLM_USAGE_DIR = ""   # no usage log / budget file in tests


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    agent._cache.clear()
    agent.budget.reset_for_tests()
    yield
    agent._cache.clear()
