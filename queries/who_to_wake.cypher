// Tool: who_to_wake
// Owning team, primary on-call, manager/members and tier for one or more services (synthetic org data).
// Params: $services list of service names
// Read-only.

// Aggregates are computed in WITH, never in a RETURN that also has 2+ grouping keys: on FalkorDB 6.0.1 such a
// RETURN panics the server (reply.rs unwrap on None) when a non-first grouping key is null.
// Team display/slack/pager default to the team name, so a Team built without them still renders.
// @query who_to_wake
MATCH (t:Team)-[:OWNS]->(s:Service)
WHERE s.name IN $services
OPTIONAL MATCH (oc:Person)-[:ONCALL_FOR]->(t)
OPTIONAL MATCH (mem:Person)-[:MEMBER_OF]->(t)
WITH s, t,
     collect(DISTINCT {name: oc.name, handle: oc.handle, email: oc.email}) AS oncall,
     collect(DISTINCT {name: mem.name, handle: mem.handle, role: mem.role}) AS members
RETURN s.name AS service, s.tier AS tier, s.env AS env, s.runtime AS runtime, s.repo AS repo,
       t.name AS team, coalesce(t.display, t.name) AS team_display, coalesce(t.slack, '#team-' + t.name) AS slack,
       coalesce(t.pager, t.name) AS pager, oncall, members
ORDER BY tier, service
