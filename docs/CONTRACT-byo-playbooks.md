# UI contract: BYO scan and Approve-then-remember

Written by the frontend owner so the UI and the backend agree. The UI is defensive, but this is the shape it renders best.
The backend worker owns `backend/app/byo/` and the playbook routes.

## Router wiring

`backend/app/main.py` includes, if importable, `router` (a `fastapi.APIRouter`) from any of:
`app.byo.routes`, `app.byo.api`, `app.playbooks`, `app.playbook_routes`, `app.byo.playbooks`.
Import errors are logged and skipped, so the API keeps working while those modules are in progress.
If you prefer to include your routers yourself, delete the guarded block.

## POST /api/byo/scan

Request: `{"github_url": "https://github.com/owner/repo"}` or `{"package_json": "<raw package.json text>"}`, optional `"depth": 1..6`.

Response (200):
```json
{
  "scan_id": "byo:3f2a...",
  "source": {"kind": "github", "label": "owner/repo", "ref": "main"},
  "outcome": "answered_with_path | not_in_graph",
  "summary": "2 of 41 dependencies could have pulled a malicious version on Sept 8 2025.",
  "exposed": [
    {"package": "debug", "bad_version": "4.4.2", "clean_version": "4.4.3", "advisory_id": "MAL-2025-46974",
     "ghsa": "GHSA-4x49-vf9v-38px",
     "path": {"via": ["owner/repo", "express@5.1.0", "debug@4.4.1"], "requirements": ["^5.1.0", "^4.4.0"],
              "requirement": "^4.4.0", "hops": 2, "exposed": true, "reason": "debug ^4.4.0 (caret) admits 4.4.2"}}
  ],
  "not_exposed": [ {"same shape": "with path.exposed=false and the reason"} ],
  "overrides": {"overrides": {"debug": "4.4.3"}},
  "graph": {"nodes": [{"id": "owner/repo", "type": "service", "status": "exposed"}],
            "edges": [{"id": "a->b", "source": "a", "target": "b", "requirement": "^4.4.0", "status": "exposed", "last": true}]},
  "trace": [{"tool": "exposure", "query": "exposure", "cypher": "...", "params": {}, "ms": 3.1, "rows": 12}],
  "stats": {"direct": 12, "versions": 410, "edges": 900, "ms": 2300}
}
```
`graph` uses the same element shape as `/api/ask` (`type`: service | package | affected, `status`: exposed | safe).
Errors: `{"error": "<code>", "message": "<human text>"}` with 4xx/5xx; the UI shows `message`.

## Playbooks

- `POST /api/incidents/{incident_id}/approve` (body optional `{"approved_by": "..."}`) returns `{"playbook": Playbook}`.
- `GET /api/playbooks` returns `{"playbooks": [Playbook]}`.

```json
{"id": "PB-...", "incident_id": "INC-...", "name": "qix compromise: pin to clean versions",
 "approved_at": "2026-10-16T04:10:00+00:00", "approved_by": "atharv",
 "trigger": "maintainer qix was phished", "overrides": {"debug": "4.4.3"}, "services": ["auth-service"],
 "steps": ["Add overrides", "Reinstall", "Redeploy"]}
```
