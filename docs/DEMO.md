# Two-minute demo script

**0:00 Hook.** "September 8, 2025: one npm maintainer, qix, got phished. 19 packages with 2 billion weekly downloads shipped malware for two hours. Your CISO asks: are we hit? PatientZero answers in seconds, from a graph."

**0:15 Ask.** Click the card *Replay the qix compromise*. Point at the fleet graph lighting up: 13 of 22 services exposed at depth 4. Open one chain: `auth-service -> ioredis@5.4.1 -> debug ^4.3.4 admits 4.4.2`. "That is a Cypher traversal plus an npm semver check on the last edge. The trace shows every query."

**0:35 Honest negatives.** Scroll to *Not exposed, with reason*: `checkout-api -> express@4.21.2 -> debug 2.6.9 (exact) excludes 4.4.2`. Move the depth slider to 1: only 1 service. "Transitive depth is the whole story."

**0:50 Insights.** Blast radius rings (hops from the package out to services), then the Exposure matrix. "Every cell is a path from the graph."

**1:05 Act.** Click *Generate fix*: overrides JSON pinned to clean versions, Slack drafts per team, who to wake. "It wrote this incident back into FalkorDB." Click *Approve*.

**1:20 Memory.** Open a new tab, ask about qix again: *Answered from graph memory*. Show the timeline: compromise, advisories, our incident, the approved playbook.

**1:35 Your repo.** Scroll to *Would you have been hit?* Click `axios/axios`: yes, through `https-proxy-agent` then `debug`. Click `express@4.x (runtime only)`: no, and why.

**1:55 Close.** "Deterministic first, the LLM only when needed and name-checked, hard spend caps, and the same tools over MCP. PatientZero."
