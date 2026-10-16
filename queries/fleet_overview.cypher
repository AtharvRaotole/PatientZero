// Tool: fleet_overview (home screen)
// Read-only.

// @query fleet_overview
MATCH (t:Team)-[:OWNS]->(s:Service)
OPTIONAL MATCH (s)-[d:DEPENDS_ON]->(v:Version)
WITH s, t, collect({pkg: v.pkg, version: v.version, requirement: d.requirement}) AS direct_deps
RETURN s.name AS service, s.tier AS tier, s.env AS env, s.runtime AS runtime, s.repo AS repo, t.name AS team,
       direct_deps
ORDER BY tier, service

// @query graph_stats
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label
