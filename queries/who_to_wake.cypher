// Tool: who_to_wake
// Owning team, primary on-call, manager/members and tier for one or more services (synthetic org data).
// Params: $services list of service names
// Read-only.

// @query who_to_wake
MATCH (t:Team)-[:OWNS]->(s:Service)
WHERE s.name IN $services
OPTIONAL MATCH (oc:Person)-[:ONCALL_FOR]->(t)
OPTIONAL MATCH (mem:Person)-[:MEMBER_OF]->(t)
RETURN s.name AS service, s.tier AS tier, s.env AS env, s.runtime AS runtime, s.repo AS repo,
       t.name AS team, t.display AS team_display, t.slack AS slack, t.pager AS pager,
       collect(DISTINCT {name: oc.name, handle: oc.handle, email: oc.email}) AS oncall,
       collect(DISTINCT {name: mem.name, handle: mem.handle, role: mem.role}) AS members
ORDER BY tier, service
