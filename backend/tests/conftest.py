"""The deterministic router is the test oracle: tests never call the LLM (and never need a key)."""
from app import config

config.AGENT_MODE = "deterministic"
