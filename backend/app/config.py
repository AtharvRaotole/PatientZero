import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv()

# FALKORDB_URL (e.g. redis://user:pass@host:port or rediss:// for TLS, as given by FalkorDB Cloud) wins over
# the individual settings below.
FALKORDB_URL = os.getenv("FALKORDB_URL") or ""
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_USERNAME = os.getenv("FALKORDB_USERNAME") or None
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
FALKORDB_SSL = os.getenv("FALKORDB_SSL", "false").lower() in ("1", "true", "yes")
GRAPH_NAME = os.getenv("FALKORDB_GRAPH", "fleet:acme")
DEFAULT_DEPTH = int(os.getenv("DEFAULT_DEPTH", "4"))
MAX_DEPTH = 6
# e.g. https://.*\.vercel\.app for preview deployments
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX") or None
CORS_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000"
).split(",") if o.strip()]

# Agent mode. auto: LLM tool calling when OPENAI_API_KEY is set, else the deterministic router.
AGENT_MODE = os.getenv("AGENT_MODE", "auto").lower()  # auto | deterministic | llm
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
LLM_MODEL = os.getenv("LLM_MODEL") or "gpt-4o-mini"
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))
