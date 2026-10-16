// Tool: recall (memory panel) + demo housekeeping. Added by the app layer.

// @query list_incidents
// Every stored incident, newest first, for the memory panel. Params: $limit. Read-only.
MATCH (i:Incident)
OPTIONAL MATCH (i)-[e:EXPOSED]->(s:Service)
RETURN i.id AS id, i.at AS at, i.trigger AS trigger, i.entity_kind AS entity_kind, i.entity_key AS entity_key,
       i.depth AS depth, i.exposed_count AS exposed_count, collect(s.name) AS services
ORDER BY at DESC LIMIT $limit

// @query incident_exposed_count
// AC9 graph check: EXPOSED edges written for one incident. Params: $id. Read-only.
MATCH (:Incident {id: $id})-[e:EXPOSED]->(:Service)
RETURN count(e) AS exposed_edges

// @query reset_memory
// WRITE. Deletes app-written memory (Incident, Finding, crew runs, sessions) so a demo can start clean, in the
// current tenant's graph only. Data graph untouched.
MATCH (n) WHERE n:Incident OR n:Finding OR n:CrewRun OR n:AgentRun OR n:Session OR n:Turn OR n:Campaign
DETACH DELETE n
RETURN count(*) AS deleted
