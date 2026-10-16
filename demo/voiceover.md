# PatientZero demo voiceover

Narration for `demo/patientzero-demo.mp4` (1:59.6). Voice: OpenAI `gpt-4o-mini-tts`, `onyx`, calm founder-demo delivery.
Each line starts 0.25 s after its beat (0.5 s for the first; a "+x" in Start delays it further) and must end before the next beat; `scripts/voiceover.py` builds
`demo/patientzero-demo-voiced.mp4` from this table (per-beat clips, cached, at most 1.08x speed-up, loudness -16 LUFS).

Sources for the numbers: 19 packages and 2.71 billion downloads are the npm downloads API totals for the 19 compromised
packages, Sept 1 to 7, 2025 (as in the app's kicker). Advisories are real OSV / GHSA records. The 22-service fleet is synthetic.

| Start | Beat | Window | Narration |
|---|---|---|---|
| 0:00.0 | home | 10.3 s | September 8th, 2025. A phished npm maintainer. Nineteen packages, 2.7 billion downloads a week, shipped malware. |
| 0:09.3 +1.0 | replay | 4.5 s | Are we hit? Graph traversal on Falkor DB answers. |
| 0:14.8 | graph lit | 10.5 s | Real OSV advisories and npm data, on a synthetic fleet. Thirteen of twenty-two services can install malware. |
| 0:25.3 | exposure | 5.6 s | Every chain ends in a real npm semver check on the last edge. |
| 0:30.9 | honest negatives | 7.0 s | Honest negatives too. Checkout API reaches debug, but its range excludes the bad version. |
| 0:37.9 | depth | 7.5 s | Direct dependencies alone show one service. Transitive depth is the whole story. |
| 0:45.4 | insights | 10.8 s | Fourteen of the nineteen compromised packages are reachable from this fleet. Every cell in the matrix is a path in the graph. |
| 0:56.2 | who to wake | 4.0 s | Owners and on-call, straight from the graph. |
| 1:00.2 | fix | 6.8 s | The fix: clean version pins, one draft per team, written back to Falkor DB. |
| 1:07.0 | approve | 5.0 s | A human approves it, and it becomes a playbook. |
| 1:12.0 | recall | 8.4 s | Later, someone types three letters. PatientZero knows what they mean. |
| 1:20.4 | memory answer | 4.8 s | It answers from graph memory, and cites the incident. |
| 1:25.2 | timeline | 5.6 s | Compromise to playbook, as dated nodes in the graph. |
| 1:30.8 | byo axios | 7.4 s | Bring your own repo. Axios was exposed at runtime, through debug. |
| 1:38.2 | byo express | 9.9 s | Express 4 pins debug 2.6.9 exactly, so its runtime was clean. We show the work either way. |
| 1:48.1 | mcp | 7.2 s | Agents get the same tools over MCP, with the same answer: thirteen of twenty-two. |
| 1:55.3 | close | 4.2 s | Know your blast radius before the postmortem. |
