"""Demo-video MCP beat: start the PatientZero MCP server over stdio, list its tools, call `remediation_plan`
(the same entry point as the UI's qix replay; it writes an incident to the LOCAL dev graph).

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
        args = {"trigger": "maintainer qix was phished", "depth": 4}
        print(f"tools/call remediation_plan {json.dumps(args)}")
        t = time.perf_counter()
        res = await s.call_tool("remediation_plan", args)
        ms = (time.perf_counter() - t) * 1000
        body = json.loads(res.content[0].text)
        out, steps = body["result"], body.get("trace", [])
        fix = out.get("fix") or {}
        svcs = [x["service"] for x in fix.get("per_service", [])]
        print(f"<- {len(steps)} graph steps in {ms:.0f} ms ({', '.join(dict.fromkeys(x['tool'] for x in steps))})")
        print(f"<- {out['message'].split('. ')[0]}.")
        print(f"<- exposed: {', '.join(svcs)}")
        if fix.get("incident_id"):
            print(f"<- incident {fix['incident_id']} written to the graph, {len(svcs)} per-service patches drafted")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
