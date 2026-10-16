// Tool: remediation_plan (WRITE: run with query(), not ro_query())
// The app computes overrides/drafts from exposure + incident_scope + who_to_wake, then persists the incident
// as graph memory with these statements, in order.

// @query create_incident
// Params: $id, $at (ISO 8601), $trigger (raw user text), $entity_kind, $entity_key, $depth, $exposed_count,
//         $patch (JSON string of the per-service overrides)
CREATE (i:Incident {id: $id, at: $at, trigger: $trigger, entity_kind: $entity_kind, entity_key: $entity_key,
                    depth: $depth, exposed_count: $exposed_count, patch: $patch})
RETURN i.id AS id

// @query link_incident_entity
// Params: $id, $entity_key. Links the incident to the resolved Maintainer/Advisory/Version/Package for recall.
MATCH (i:Incident {id: $id}), (n)
WHERE (n:Maintainer AND n.npm = $entity_key) OR (n:Advisory AND n.id = $entity_key)
   OR (n:Version AND n.key = $entity_key) OR (n:Package AND n.name = $entity_key)
CREATE (i)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query link_exposed
// Params: $id, $rows = [{service, path: [names], requirement, pkg, advisory, hops}]
UNWIND $rows AS r
MATCH (i:Incident {id: $id}), (s:Service {name: r.service})
CREATE (i)-[:EXPOSED {path: r.path, via_requirement: r.requirement, pkg: r.pkg, advisory: r.advisory, hops: r.hops}]->(s)
RETURN count(*) AS exposed_edges

// @query add_findings
// Params: $id, $at, $session, $rows = [{service, text}]
UNWIND $rows AS r
MATCH (i:Incident {id: $id}), (s:Service {name: r.service})
CREATE (i)-[:HAS_FINDING]->(f:Finding {text: r.text, at: $at, session: $session, incident: $id})-[:ABOUT]->(s)
RETURN count(f) AS findings
