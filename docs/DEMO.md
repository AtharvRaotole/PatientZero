# Two-minute demo: shot list

Every quoted string below is on screen exactly as written. Record at 1280x800 or larger, browser zoom 100%.

**Before recording.** Run `sh scripts/reset-live.sh` (this script shows the Fix and Approve clicks live, so memory
starts empty). Leave the mode on **Auto** in the top bar. Hover the info icon next to it once if you want to show
what Auto and Deterministic mean. Start at the top of the page.

| Time | On screen | Do | Say |
|---|---|---|---|
| 0:00 | First screen. Kicker: "Sept 8, 2025. One phished maintainer, 19 packages, 2.71 billion downloads the week before." Fleet graph: "22 services", "1,120 package versions", "2,283 edges". | Nothing. Let the fleet graph draw in. | "September 8, 2025: one npm maintainer, qix, was phished. 19 of his packages, 2.71 billion downloads the week before, shipped malware. Your CISO asks: are we hit?" |
| 0:12 | Button "Replay the qix compromise" under the command bar. | Click it. | "PatientZero answers from a FalkorDB graph of our 22 services." |
| 0:15 | Exposed chains draw in vermilion, from each compromised package out to the services. Headline: "13 of 22 services can install a malicious version." Under the command bar: "Last traversal ... ms / 6 Cypher queries / 174 rows read". | Point at a red package label, e.g. "debug@4.4.2 (via 8 ranges)". | "Red is a range that admits the malicious version. The label is the bad version, not what is installed today. Six hand-written Cypher queries, milliseconds." |
| 0:28 | Section "02 Exposure", tab "Exposed 13". Row "auth-service": "via ioredis@5.4.1 → debug ^4.3.4 admits 4.4.2". | Scroll to it. | "Every chain ends in an npm semver check on the last edge." |
| 0:36 | Tab "Excluded by range 4". Row "checkout-api": "via express@4.21.2 → debug 2.6.9 excludes 4.4.2", reason "debug 2.6.9 (exact) does not satisfy 4.4.2". | Click the tab. | "Honest negatives: checkout-api reaches debug, but its range rules the bad version out." |
| 0:44 | "Depth" slider in the fleet graph header. Headline becomes "1 of 22 services can install a malicious version."; only "shipping-tracker" and "debug@4.4.2 (via ^4.3.7)" are red. | Scroll up, drag Depth to 1, wait, then back to 4. | "Look only at direct dependencies and you find 1. Transitive depth is the whole story." |
| 0:55 | "Insights": "14 of 19 compromised packages reachable from the fleet". "Blast radius" rings with the core "14 of 19 compromised packages". | Hover the core so it fans out, then click "Exposure matrix". | "14 of the 19 are reachable from our fleet. Every cell in the matrix is a path in the graph." |
| 1:07 | "03 Who to wake": "13 owning teams" with on-call names. Then "04 Fix", card "Remediation". | Click "Generate fix". | "Owners and on-call come from the graph too." |
| 1:13 | "Incident INC-... written to the graph with ... exposure edges." "Fleet-wide overrides" shows "debug@^4": "4.4.3". | Click "Approve". It reads "Approved and remembered". | "Overrides pinned to the clean versions, one draft per team. It wrote the incident back into FalkorDB, and I approve the plan as a playbook." |
| 1:22 | Command bar. | Type `qix` and press Enter. | "Now ask again." |
| 1:25 | Headline "Answered from graph memory." with "Incident INC-... / <date and time>". | Point at the incident line. | "Answered from graph memory, citing the incident and when. "Run a fresh traversal" re-checks today's graph." |
| 1:32 | "Timeline", "From compromise to remediation". | Scroll to it. | "Compromise, OSV advisories, our incident and the approved playbook, all dated nodes." |
| 1:40 | "Would you have been hit on Sept 8, 2025?" | Under "Examples", click "axios/axios". It scans right away. | "Bring your own repo: it resolves the real dependency graph into a separate FalkorDB graph." |
| 1:46 | "Runtime exposed. A fresh install could have pulled malware." Then "Exposed via axios/axios → https-proxy-agent → debug (debug@4.4.2), plus 1 more runtime path." and a quieter "Dev tooling: ..." line. | Point at the runtime line. | "axios was exposed at runtime through https-proxy-agent, then debug." |
| 1:52 | Click "expressjs/express@4.x (runtime only)". It scans right away. "Runtime clean." Card "Excluded by range": "expressjs/express → debug 2.6.9 excludes 4.4.2" and, in green, "Excluded by range: debug 2.6.9 (exact) does not satisfy 4.4.2." | Click it. | "Express 4 pins debug 2.6.9 exactly, so it was clean." |
| 1:58 | Same screen. | Stop. | "Deterministic first, the LLM only when needed and name-checked, hard spend caps. PatientZero." |

Notes
- The 2.71 billion figure is the npm downloads API total for the 19 packages, Sept 1 to 7, 2025 (hover the kicker for the source).
- If memory was seeded with `sh scripts/seed-demo-memory.sh` instead, skip the Approve click at 1:13; the "qix" ask still says "Answered from graph memory."
- Exact counts (13, 4, 1, 14 of 19) are for the synthetic Acme fleet at depth 4 and depth 1, as loaded.
