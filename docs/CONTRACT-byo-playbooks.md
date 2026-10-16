# UI contract: BYO scan and Approve-then-remember

Written by the frontend owner so the UI and the backend agree. The UI is defensive, but this is the shape it renders best.
The backend worker owns `backend/app/byo/` and the playbook routes.

## Router wiring

`backend/app/main.py` includes, if importable, `router` (a `fastapi.APIRouter`) from any of:
`app.byo.routes`, `app.byo.api`, `app.playbooks`, `app.playbook_routes`, `app.byo.playbooks`.
Import errors are logged and skipped, so the API keeps working while those modules are in progress.
If you prefer to include your routers yourself, delete the guarded block.

## POST /api/byo/scan

Request: `{"github_url": "https://github.com/owner/repo"}` or `{"package_json": "<raw package.json text>"}`, optional `"depth": 1..6`, `"include_dev": true|false` (default true).

Response (200):
```json
{
  "scan_id": "byo:3f2a...",
  "source": {"kind": "github", "label": "owner/repo", "ref": "main"},
  "outcome": "answered_with_path | not_in_graph",
  "summary": "Runtime: exposed via axios/axios → https-proxy-agent → debug (debug@4.4.2), plus 1 more runtime path. Dev tooling: 66 more paths to ... via @babel/core, @commitlint/cli and 2 more.",
  "details": {
    "headline": "same as summary",
    "would_have_been_hit": true,
    "runtime_hit": true,
    "verdict_runtime": {"status": "exposed | clean", "packages": ["debug@4.4.2"], "path_count": 2,
                        "paths": [{"package": "debug@4.4.2", "via": ["axios/axios", "https-proxy-agent@5.0.1", "debug@4.4.3"],
                                   "chain": "axios/axios → https-proxy-agent → debug"}],
                        "text": "Runtime: exposed via ... | Runtime clean."},
    "verdict_dev": {"status": "exposed | clean | not_scanned", "packages": ["chalk@5.6.1"], "path_count": 66,
                    "via": ["@commitlint/cli", "eslint"],
                    "text": "Dev tooling: N more paths ... | Dev tooling only: ... | Dev tooling clean. | Dev tooling: not scanned (devDependencies excluded)."},
    "payload_note": "The Sept 2025 payload hooked browser crypto-wallet APIs, so runtime and bundled code matter most; dev-only exposure hits developer and CI machines.",
    "runtime_exposed": ["debug@4.4.2"], "runtime_paths": ["display strings of every runtime path"],
    "skipped_direct": 0, "direct_dependencies": 41, "lockfile": "...", "assumption": "..."
  },
  "exposed": [
    {"package": "debug", "bad_version": "4.4.2", "clean_version": "4.4.3", "advisory_id": "MAL-2025-46974",
     "ghsa": "GHSA-4x49-vf9v-38px",
     "path": {"via": ["owner/repo", "express@5.1.0", "debug@4.4.1"], "requirements": ["^5.1.0", "^4.4.0"],
              "requirement": "^4.4.0", "hops": 2, "exposed": true, "reason": "debug ^4.4.0 (caret) admits 4.4.2"},
     "via_dev_dependency": false,
     "runtime_paths": ["owner/repo → https-proxy-agent@5.0.1 → debug 4 ⚠ 4.4.2"],
     "dev_paths": ["first 10 dev path display strings"], "dev_path_count": 33, "dev_entries": ["@commitlint/cli"]}
  ],
  "not_exposed": [ {"same shape": "with path.exposed=false and the reason"} ],
  "overrides": {"dependencies": {"debug": "^4.4.3"}, "overrides": {"debug@^4": "^4.4.3", "ansi-styles@^6": "6.2.3"}},
  "graph": {"nodes": [{"id": "owner/repo", "type": "service", "status": "exposed"}],
            "edges": [{"id": "a->b", "source": "a", "target": "b", "requirement": "^4.4.0", "status": "exposed", "last": true}]},
  "trace": [{"tool": "exposure", "query": "exposure", "cypher": "...", "params": {}, "ms": 3.1, "rows": 12}],
  "stats": {"direct": 12, "versions": 410, "edges": 900, "ms": 2300}
}
```
Render runtime first: `verdict_runtime.text`, then `verdict_dev.text`, then `payload_note`. `via_dev_dependency` is
true only when EVERY exposed path of that finding enters through a devDependency; the primary `path` is a runtime path
whenever one exists. `overrides` is the install-safe package.json patch (direct deps bumped, transitive overrides
scoped to the compromised major).

`graph` uses the same element shape as `/api/ask` (`type`: service | package | affected, `status`: exposed | safe).
Errors: `{"error": "<code>", "message": "<human text>"}` with 4xx/5xx; the UI shows `message`.

## Playbooks

- `POST /api/incidents/{incident_id}/approve` (body optional `{"approved_by": "..."}`) returns `{"playbook": Playbook}`.
- `GET /api/playbooks` returns `{"playbooks": [Playbook]}`.

```json
{"id": "PB-...", "incident_id": "INC-...", "name": "qix compromise: 8 scoped pin(s) across 13 service patch(es)",
 "approved_at": "2026-10-16T04:10:00+00:00", "approved_by": "atharv",
 "trigger": "maintainer qix was phished",
 "overrides": {"debug@^4": "4.4.3", "ansi-styles@^6": "6.2.3"},
 "service_patches": {"shipping-tracker": {"dependencies": {"debug": "^4.4.3"}, "overrides": {"debug@^4": "^4.4.3"}},
                     "auth-service": {"overrides": {"debug@^4": "4.4.3"}}},
 "services": ["auth-service", "shipping-tracker"],
 "steps": ["Merge each service's own package.json patch (service_patches) ...", "Delete node_modules ...", "Reinstall ...", "Redeploy ..."]}
```

## Trace step names
Memory lookups appear as `tool: "memory lookup"` with `note` = `no prior incidents` or `N prior incident(s) found in
graph memory` (instead of a bare "recall, 0 rows").
