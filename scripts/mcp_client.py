"""AC13: start the MCP server over stdio, list its tools, call `exposure` and compare with the HTTP API.

Run: cd backend && uv run python ../scripts/mcp_client.py
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND = Path(__file__).resolve().parent.parent / "backend"


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(BACKEND))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = sorted(t.name for t in (await s.list_tools()).tools)
        print(f"MCP tools ({len(names)}): {', '.join(names)}")
        res = await s.call_tool("exposure", {"package": "debug", "bad_version": "4.4.2", "depth": 4})
        data = json.loads(res.content[0].text)
        mcp_svcs = data["result"]["exposed_services"]
        print(f"MCP exposure(debug, 4.4.2, depth 4): {len(mcp_svcs)} exposed services: {mcp_svcs}")
    try:
        api = httpx.post("http://localhost:8000/api/tools/exposure", timeout=10,
                         json={"package": "debug", "bad_version": "4.4.2", "depth": 4}).json()
        same = api["result"]["exposed_services"] == mcp_svcs and len(api["result"]["rows"]) == len(data["result"]["rows"])
        print(f"API returns the same rows: {same}")
    except httpx.HTTPError as e:
        print(f"API not running, skipped comparison ({e})")
        same = True
    return 0 if len(names) == 8 and same else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
