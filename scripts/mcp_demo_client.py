"""Demo-video MCP beat: start the PatientZero MCP server over stdio, list its tools, call `exposure`.

Prints a short transcript; scripts/record_demo.cjs replays this REAL output in a terminal card.
Run: cd backend && uv run python ../scripts/mcp_demo_client.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND = Path(__file__).resolve().parent.parent / "backend"


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(BACKEND))
    print("spawn MCP server over stdio: python -m app.mcp_server")
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        print(f"connected: {init.serverInfo.name} (protocol {init.protocolVersion})")
        names = sorted(t.name for t in (await s.list_tools()).tools)
        print(f"tools/list -> {len(names)} tools: {', '.join(names)}")
        args = {"package": "debug", "bad_version": "4.4.2", "depth": 4}
        print(f"tools/call exposure {json.dumps(args)}")
        t = time.perf_counter()
        res = await s.call_tool("exposure", args)
        ms = (time.perf_counter() - t) * 1000
        data = json.loads(res.content[0].text)["result"]
        svcs = data["exposed_services"]
        print(f"<- {len(svcs)} exposed services in {ms:.0f} ms: {', '.join(svcs)}")
        row = next((x for x in data.get("rows", []) if x.get("exposed")), None)
        if row:
            via = " -> ".join(row.get("via") or row.get("path") or [])
            print(f"<- e.g. {via}  [{row.get('reason', '')}]")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
