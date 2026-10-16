"""MCP client mode for the agent: /api/ask selects and runs its tools through PatientZero's own MCP server.

See client.py (transport and calls), server.py (the `investigate` tool added to the shared MCP server) and
domain.py (the domain wrapper the agent loop uses). Docs: docs/CONTRACT-watch-mcp-bench.md.
"""
from .domain import McpDomain, wrap_domain  # noqa: F401
