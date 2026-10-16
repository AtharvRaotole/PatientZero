# PatientZero

> When an npm maintainer gets phished, PatientZero tells you in seconds which of your services are hit, the exact dependency chain, who to wake, and the fix. Built on FalkorDB.

**Track:** FalkorDB Hackathon, Track 1 (Agents That Act on Connected Data)

PatientZero replays the real September 8, 2025 npm compromise (maintainer `qix` phished, 19 malicious versions of
`debug`, `chalk`, `ansi-styles` and friends) against a fleet graph in FalkorDB. Every answer is a Cypher traversal
plus an npm semver check on the last edge, shown with its trace. Nothing is guessed.

| Data | Real or synthetic |
|---|---|
| Malicious versions, advisories (OSV MAL ids, GHSA), clean versions | **Real** (OSV, GitHub advisories, debug-js/debug#1005) |
| Dependency edges and declared ranges | **Real** (deps.dev resolution of real npm versions) |
| npm maintainers | **Real** (registry.npmjs.org) |
| The Acme fleet: 22 services, 8 teams, 16 people, on-call | **Synthetic** (labelled as such in the UI) |
| Bring-your-own repo scans | **Real** (your public GitHub repo or package.json, resolved live via deps.dev) |

## What you can do
- **Ask** in plain English: "qix was phished, who is affected?", "debug@4.4.2", "GHSA-4x49-vf9v-38px", "who do I wake for checkout-api", "where are our single points of failure?", "how does auth-service reach simple-swizzle?".
- **See** the blast radius on the live fleet graph, with exposed chains animated, a depth slider (1 to 6 hops) and honest negatives ("checkout-api reaches debug through express@4.21.2, but the exact pin 2.6.9 excludes 4.4.2").
- **Insights**, all derived from the returned graph paths:
  - *Blast radius rings*: compromised package in the center, one ring per hop out to the services.
  - *Exposure matrix*: services x compromised packages, shaded by the number of admitting paths.
  - *Reach vs PageRank*: maintainer single points of failure (fleet reach from Cypher, `algo.pageRank` over the dependency graph).
  - *Incident timeline*: every dated event in the graph, from the compromise and OSV advisories to incidents and approved playbooks.
- **Fix**: per-service `overrides` JSON pinned to clean versions, Slack drafts per owning team, written back to the graph as an `(:Incident)`.
- **Remember**: ask again in a new session and the answer comes from graph memory. **Approve** a fix and it becomes a `(:Playbook)` that later incidents on the same packages reuse.
- **Bring your own repo**: "Would you have been hit on Sept 8, 2025?" Paste a GitHub URL or package.json. Try `github.com/axios/axios` (exposed through `https-proxy-agent` then `debug`) and `github.com/expressjs/express/tree/4.x` with runtime deps only (clean).
- **Optional Shai-Hulud worm**: load with `INCIDENTS=qix,shai-hulud` and ask "were we hit by the Shai-Hulud worm?".

## Agent design
- A **deterministic intent router** picks among 8 hand-written tools (`queries/*.cypher`). It is the default path, the fallback and the test oracle.
- With `OPENAI_API_KEY` set, the **LLM is used only when the router is not confident**. It can only call the same tools; it never writes Cypher. Every package or service name in its answer is checked against the graph results and unverified names are flagged in the UI.
- The **AI incident summary** runs only when the user clicks for it, and is name-checked the same way.
- **Cost controls**: `OPENAI_MODEL` (default gpt-4o-mini), max tokens 400 (tools) and 250 (summary), answer cache per (question, graph), a server-side daily call cap (`OPENAI_DAILY_CALL_CAP`, default 200) and a per-client limit (`OPENAI_PER_IP_PER_MIN`, default 10). Over a limit, the answer comes from the deterministic router with a visible note in the trace. Token usage is logged to `.cache/llm_usage.jsonl`. The trace always shows whether the LLM was used and its token count. Tests never call the API.

## Stack
- **Graph DB:** FalkorDB 6.0.1, as a Redis module (`falkordb.so` in `redis-server`, no Docker needed locally) or FalkorDB Cloud (`FALKORDB_URL`)
- **Backend:** Python 3.13, FastAPI, `falkordb` client, `uv` (`backend/`); MCP server with the same tools
- **Frontend:** Next.js 15, TypeScript, Tailwind v4, framer-motion, react-force-graph-2d (`frontend/`)

## Prerequisites
- Linux x86_64 with `redis-server` 7+ (`sudo apt-get install -y redis-server libgomp1`), or Docker for the production image
- Python 3.13 + [uv](https://docs.astral.sh/uv/), Node 20+

## Setup and run (local)
```bash
cp .env.example .env                 # defaults work locally; OPENAI_API_KEY is optional
./scripts/start-falkordb.sh          # downloads falkordb.so (v6.0.1) on first run, starts redis-server on :6379 (AOF on)
python3 -m venv ingest/.venv && ingest/.venv/bin/pip install -r ingest/requirements.txt
ingest/.venv/bin/python ingest/load.py          # builds graph fleet:acme from data/ (under 1 s, idempotent)
(cd backend && uv sync)
(cd frontend && npm install)
./scripts/dev.sh                     # backend :8000, frontend http://localhost:3001
```

Single-origin production build (what the Dockerfile and Railway run): see [DEPLOY.md](DEPLOY.md).

## Tests
```bash
cd backend && uv run pytest -q                                              # unit + integration (needs FalkorDB with fleet:acme)
ingest/.venv/bin/python ingest/verify.py                                   # data-level acceptance checks
NODE_PATH=/path/to/node_modules node scripts/e2e_screenshots.cjs           # 12 browser checks, screenshots in docs/screenshots
```

## API
| Method | Path | Description |
|---|---|---|
| GET | `/health` | FalkorDB status, graph, agent mode, LLM model, tools, LLM budget |
| POST | `/api/ask` | `{query, depth?, fresh?, session?, mode?}`: the agent. Returns outcome, message, paths, graph, wake, trace |
| POST | `/api/fix` | Same, plus remediation plan written to the graph as an Incident |
| POST | `/api/summary` | Optional one-paragraph AI summary, name-checked |
| GET | `/api/fleet`, `/api/fleet/graph` | Fleet services and the full graph for the hero view |
| GET | `/api/incidents` | Incidents in graph memory |
| GET | `/api/timeline` | Dated events: compromise, advisories, incidents, playbooks |
| POST | `/api/memory/reset` | Clear incidents (demo reset) |
| GET | `/api/tools`, POST `/api/tools/{name}` | The tool catalogue, callable directly |
| POST | `/api/byo/scan` | `{github_url \| package_json, include_dev?, depth?}`: scan your repo in an isolated graph |
| POST | `/api/incidents/{id}/approve`, GET `/api/playbooks` | Approve-then-remember |

## MCP server (Claude Desktop, Cursor)
The same 8 tools over stdio. Add to `claude_desktop_config.json` or `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "patientzero": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/repo/backend", "run", "python", "-m", "app.mcp_server"],
      "env": { "FALKORDB_HOST": "localhost", "FALKORDB_PORT": "6379", "FALKORDB_GRAPH": "fleet:acme" }
    }
  }
}
```

## Graph data model
See [ingest/README.md](ingest/README.md) for the full model, sources and the exposure rule. In short:
`(:Team)-[:OWNS]->(:Service)-[:DEPENDS_ON]->(:Version)-[:REQUIRES*]->(:Version)<-[:HAS_VERSION]-(:Package)`,
`(:Advisory)-[:AFFECTS]->(:Version)`, `(:Maintainer)-[:MAINTAINS|HAD_PUBLISH_RIGHTS]->(:Package)`, and app write-backs
`(:Incident)-[:EXPOSED]->(:Service)`, `(:Playbook)-[:APPROVED_FROM]->(:Incident)`.

## Demo script
See [docs/DEMO.md](docs/DEMO.md).

## AI assistance disclosure
Built with AI coding assistants. All Cypher in `queries/` is hand-reviewed; the runtime LLM (optional) can only call those tools.
