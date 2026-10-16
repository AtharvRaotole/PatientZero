# PatientZero demo voiceover

Narration for `demo/patientzero-demo-voiced.mp4`, in the beat order of `docs/DEMO.md` (the 2:00 shot list).
Voice: OpenAI `gpt-4o-mini-tts`, `onyx`, calm founder-demo delivery. "Falkor DB" is spelled apart so it is spoken as two words.

How it is built:
1. `python3 scripts/voiceover.py --clips` makes one TTS clip per beat (cached in `demo/vo/` by text hash) and writes
   `demo/vo/durations.json`.
2. `scripts/record_demo.cjs` records the live site and holds every beat at least as long as its clip, so a line
   never runs into the next beat. It writes `demo/beats.json`.
3. `python3 scripts/voiceover.py` places each clip 0.3 s after its beat starts, writes the captions
   (`demo/patientzero-demo.srt`, one cue per sentence), burns them in, and writes the silent
   `demo/patientzero-demo.mp4` and the voiced `demo/patientzero-demo-voiced.mp4`.

Sources for the numbers: 19 packages and 2.71 billion downloads are the npm downloads API totals for the 19
compromised packages, Sept 1 to 7, 2025 (as in the app's kicker). Advisories are real OSV and GHSA records. The fleets
are synthetic. The benchmark figures are the last saved run on the deploy (in process, synthetic scratch writes,
retry on busy; about 49 req/s end to end over HTTP at 30 clients).

| Beat | DEMO.md | Narration |
|---|---|---|
| hook | 0:00 Hook | September 8th, 2025. npm maintainer qix was phished, and nineteen of his packages shipped malware. Are we hit? |
| replay | 0:08 Multi-hop answer | Every chain is a multi-hop traversal in Falkor DB, with an npm semver check on the last edge. Honest negatives: checkout API reaches debug, but its range rules the bad version out. Direct dependencies alone find one. Depth is the whole story. |
| crew | 0:25 Crew (handoffs) | Four deterministic specialists. Each reads its inputs back from the graph, and every finding and handoff is a node or edge in Falkor DB, so this run replays from a link. Pages are drafts, never sent. |
| followups | 0:50 Follow-ups | Sessions live in the graph too. A follow-up resolves against earlier turns, and asking who to wake gives the teams and on-call people first. |
| fix | 1:05 Act and remember | It writes the incident into Falkor DB, I approve the plan as a playbook, and the next time it answers from graph memory. |
| tenants | 1:15 Tenants | Each tenant is its own graph. Globex is hit by Shai-Hulud, and Acme is not. Nothing from Acme's memory or sessions shows up in Globex. |
| watch | 1:28 Live watch | A real feed: npm malware advisories from OSV, checked against every tenant within one poll, with their real publish dates. This button replays a past advisory, labelled as a replay. |
| hood | 1:40 Under the hood | Every answer is traced: each MCP tool call, then parameterised Cypher, never generated from your text. The benchmark runs in process, on synthetic scratch writes. End to end over HTTP, it is about fifty requests a second. |
| close | 1:52 Close | Deterministic first, every answer traced, memory in the graph. PatientZero. |
