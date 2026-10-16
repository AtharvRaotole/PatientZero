// Tool: fleet_graph (home screen hero visualization). Added by the app layer.
// The whole fleet dependency graph: Service -> direct Version, and Version -> Version resolved edges.
// Unpublished (malicious) versions are excluded: no resolved edge points at them. Read-only.

// @query fleet_graph_edges
MATCH (s:Service)-[d:DEPENDS_ON]->(v:Version)
RETURN s.name AS source, v.name AS target, d.requirement AS requirement, 'DEPENDS_ON' AS type
UNION ALL
MATCH (a:Version)-[r:REQUIRES]->(b:Version)
RETURN a.name AS source, b.name AS target, r.requirement AS requirement, 'REQUIRES' AS type

// @query fleet_graph_nodes
MATCH (t:Team)-[:OWNS]->(s:Service)
RETURN s.name AS id, 'Service' AS kind, s.tier AS tier, t.name AS team, false AS malicious
UNION ALL
MATCH (v:Version)
WHERE v.unpublished = false
RETURN v.name AS id, 'Version' AS kind, null AS tier, null AS team, v.malicious AS malicious
