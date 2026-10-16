// Tool: playbook (approve-then-remember, procedural memory).
// A human approves an incident's proposed npm overrides; the decision is stored as a (:Playbook) node linked to
// the Incident it came from, the Packages it pins and the Advisories it mitigates. The next incident that
// touches any of those packages finds it by traversal and reuses it (APPLIED_PLAYBOOK edge).
//
// (:Playbook {id, name, trigger_kind, trigger, approved_at, approved_by, overrides (JSON), service_patches (JSON),
//             services, steps})
//   -[:APPROVED_FROM]->(:Incident)
//   -[:COVERS {spec, pin}]->(:Package)
//   -[:MITIGATES]->(:Advisory)
// (:Incident)-[:APPLIED_PLAYBOOK {at}]->(:Playbook)
//
// FalkorDB v6.0.1 note: a pattern comprehension that names its relationship (e.g. [(pb)-[c:COVERS]->(p) | ...])
// made the NEXT comprehension in the same RETURN come back empty, so relationships stay unnamed below.

// @query playbook_incident
// Read. The incident to approve, with its patch and exposed services. Params: $id
MATCH (i:Incident {id: $id})
RETURN i.id AS id, i.at AS at, i.trigger AS trigger, i.entity_kind AS entity_kind, i.entity_key AS entity_key,
       i.patch AS patch, i.exposed_count AS exposed_count,
       [(i)-[:EXPOSED]->(s:Service) | s.name] AS services

// @query playbook_for_incident
// Read. Existing playbook approved from this incident (approve is idempotent). Params: $id
MATCH (pb:Playbook)-[:APPROVED_FROM]->(:Incident {id: $id})
RETURN properties(pb) AS playbook

// @query playbook_create
// WRITE. Params: $id, $incident_id, $name, $trigger_kind, $trigger, $approved_at, $approved_by, $overrides
//        (JSON, fleet-wide transitive overrides scoped by major), $service_patches (JSON, per-service package.json
//        patches incl. dependency bumps), $services, $steps
MATCH (i:Incident {id: $incident_id})
CREATE (pb:Playbook {id: $id, name: $name, trigger_kind: $trigger_kind, trigger: $trigger,
                     approved_at: $approved_at, approved_by: $approved_by, overrides: $overrides,
                     service_patches: $service_patches,
                     services: $services, steps: $steps, incident_id: $incident_id,
                     uses_count: 0})-[:APPROVED_FROM]->(i)
RETURN pb.id AS id

// @query playbook_link_packages
// WRITE. Params: $id, $rows = [{pkg, spec, pin}], e.g. {pkg:'debug', spec:'debug@^4', pin:'4.4.3'}
UNWIND $rows AS r
MATCH (pb:Playbook {id: $id}), (p:Package {name: r.pkg})
CREATE (pb)-[:COVERS {pin: r.pin, spec: r.spec}]->(p)
RETURN count(*) AS covers

// @query playbook_link_advisories
// WRITE. Every advisory affecting a covered package. Params: $id
MATCH (pb:Playbook {id: $id})-[:COVERS]->(:Package)-[:HAS_VERSION]->(:Version)<-[:AFFECTS]-(a:Advisory)
WITH DISTINCT pb, a
CREATE (pb)-[:MITIGATES]->(a)
RETURN count(*) AS mitigates

// @query playbook_list
// Read. Params: $limit
MATCH (pb:Playbook)
RETURN properties(pb) AS playbook,
       [(pb)-[:COVERS]->(p:Package) | p.name] AS packages,
       [(pb)-[:MITIGATES]->(a:Advisory) | a.id] AS advisories,
       [(j:Incident)-[:APPLIED_PLAYBOOK]->(pb) | j.id] AS reused_by
ORDER BY pb.approved_at DESC LIMIT $limit

// @query playbook_match
// Read. Approved playbooks covering any of $pkgs, best overlap first. Params: $pkgs
MATCH (pb:Playbook)-[c:COVERS]->(p:Package)
WHERE p.name IN $pkgs
WITH pb, collect({pkg: p.name, pin: c.pin, spec: c.spec}) AS matched
OPTIONAL MATCH (pb)-[:APPROVED_FROM]->(src:Incident)
RETURN properties(pb) AS playbook, matched, src.id AS source_incident, src.at AS source_at,
       coalesce(pb.uses_count, 0) AS uses_count
ORDER BY uses_count DESC, size(matched) DESC, playbook.approved_at DESC

// @query playbook_apply
// WRITE. Record that an incident reused a playbook. Params: $incident_id, $playbook_id, $at
MATCH (i:Incident {id: $incident_id}), (pb:Playbook {id: $playbook_id})
MERGE (i)-[r:APPLIED_PLAYBOOK]->(pb)
SET r.at = $at
RETURN count(r) AS applied

// @query playbook_use
// WRITE. Procedural memory: a successful reuse (today's pins agree with the playbook) bumps uses_count and
// last_used, so the Fixer prefers the most reused playbook next time. Params: $playbook_id, $at
MATCH (pb:Playbook {id: $playbook_id})
SET pb.uses_count = coalesce(pb.uses_count, 0) + 1, pb.last_used = $at
RETURN pb.uses_count AS uses_count, pb.last_used AS last_used

// @query playbook_reset
// WRITE. Demo housekeeping: delete all playbooks (procedural memory). Data graph untouched.
MATCH (pb:Playbook)
DETACH DELETE pb
RETURN count(*) AS deleted
