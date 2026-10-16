// Tool: crew (four agents that hand off through shared graph state)
// (:CrewRun {id, query, tenant, depth, session_id, started, finished, ms, status})-[:RAN]->(:AgentRun)
// (:CrewRun)-[:TRIAGED]->(:Incident)
// (:AgentRun {id, run_id, agent, started, ms, status})-[:PRODUCED]->(:Finding {id, kind, agent, title, text, data})
// (:Incident)-[:HAS_FINDING]->(:Finding)
// (:AgentRun)-[:HANDED_OFF_TO {reason, at}]->(:AgentRun)        Triage -> Investigator, Triage -> Responder,
//                                                              Investigator -> Fixer, Responder -> Fixer (a DAG)
// Every agent reads its inputs with crew_read_upstream: the findings of every AgentRun that hands off to it,
// directly or transitively. Nothing is passed between agents in Python.

// @query crew_create_run
// WRITE. Creates the run and its four AgentRun nodes (status 'queued'). Params: $id, $query, $tenant, $depth,
//        $session_id, $started, $agents = [{id, agent}]
CREATE (r:CrewRun {id: $id, query: $query, tenant: $tenant, depth: $depth, session_id: $session_id,
                   started: $started, status: 'running'})
WITH r
UNWIND $agents AS a
CREATE (r)-[:RAN]->(:AgentRun {id: a.id, run_id: $id, agent: a.agent, status: 'queued', tenant: $tenant})
RETURN count(*) AS agents

// @query crew_agent_start
// WRITE. Params: $id, $started
MATCH (a:AgentRun {id: $id})
SET a.status = 'running', a.started = $started
RETURN a.agent AS agent

// @query crew_agent_finish
// WRITE. Params: $id, $status, $ms, $finished, $error, $summary
MATCH (a:AgentRun {id: $id})
SET a.status = $status, a.ms = $ms, a.finished = $finished, a.error = $error, a.summary = $summary
RETURN a.agent AS agent

// @query crew_handoff
// WRITE. Params: $from, $to, $reason, $at, $seq
MATCH (a:AgentRun {id: $from}), (b:AgentRun {id: $to})
CREATE (a)-[h:HANDED_OFF_TO {reason: $reason, at: $at, seq: $seq}]->(b)
RETURN a.agent AS from_agent, b.agent AS to_agent

// @query crew_add_finding
// WRITE. Params: $agent_run, $incident ('' when no incident), $id, $kind, $agent, $title, $text, $data (JSON), $at,
//        $run_id, $seq (order within the run)
MATCH (a:AgentRun {id: $agent_run})
CREATE (a)-[:PRODUCED]->(f:Finding {id: $id, kind: $kind, agent: $agent, title: $title, text: $text, data: $data,
                                     at: $at, run_id: $run_id, incident: $incident, source: 'crew', seq: $seq})
WITH f
OPTIONAL MATCH (i:Incident {id: $incident})
FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | CREATE (i)-[:HAS_FINDING]->(f))
RETURN f.id AS id

// @query crew_read_upstream
// Read. Findings of every agent upstream of $id along HANDED_OFF_TO (1 to 3 hops), oldest first.
// This is how an agent learns what the previous agents found: by traversing the handoff graph.
MATCH (src:AgentRun)-[:HANDED_OFF_TO*1..3]->(:AgentRun {id: $id})
WITH DISTINCT src
MATCH (src)-[:PRODUCED]->(f:Finding)
RETURN src.agent AS agent, src.id AS agent_run, f.id AS id, f.kind AS kind, f.title AS title, f.data AS data,
       f.at AS at, f.seq AS seq
ORDER BY seq

// @query crew_adopt_incident
// WRITE. One Incident per (tenant graph, trigger): if none carries this memory key yet, adopt the most recently
// seen Incident with the same trigger at ANY depth (one written by Generate fix, or a legacy per-depth one keyed
// 'qix|d4') so the crew and Generate fix update it instead of duplicating it. Params: $crew_key, $key
OPTIONAL MATCH (c:Incident {crew_key: $crew_key})
WITH c WHERE c IS NULL
MATCH (i:Incident {entity_key: $key})
WITH i ORDER BY coalesce(i.last_seen, i.at) DESC LIMIT 1
SET i.crew_key = $crew_key
RETURN i.id AS id

// @query crew_upsert_incident
// WRITE, atomic. Creates or updates THE Incident for a trigger (memory key = entity key, depth is an attribute
// that follows the latest triage) and returns what it looked like BEFORE, so the caller can report what changed.
// Used by the crew (with $run_id) and by Generate fix ($run_id null, $patch set). History keeps the last 20
// triages. Params: $crew_key, $id, $at, $trigger, $kind, $key, $depth, $services, $run_id, $session, $patch,
// $source ('crew' | 'fix'), $hist (JSON string)
MERGE (i:Incident {crew_key: $crew_key})
ON CREATE SET i.id = $id, i.at = $at, i.trigger = $trigger, i.entity_kind = $kind, i.entity_key = $key,
              i.depth = $depth, i.created_by_run = $run_id, i.created_session = $session, i.source = $source
WITH i, coalesce(i.exposed_services, [(i)-[:EXPOSED]->(s:Service) | s.name]) AS before, i.last_seen AS prev_seen,
     coalesce(i.triage_count, 0) AS prev_count, i.id = $id AS created, i.depth AS prev_depth
SET i.first_seen = coalesce(i.first_seen, i.at), i.last_seen = $at, i.triage_count = prev_count + 1,
    i.exposed_services = $services, i.exposed_count = size($services), i.last_run = $run_id,
    i.last_session = $session, i.depth = $depth, i.last_trigger = $trigger, i.patch = coalesce($patch, i.patch),
    i.history = (coalesce(i.history, []) + [$hist])[-20..]
WITH i, before, prev_seen, prev_count, created, prev_depth
OPTIONAL MATCH (r:CrewRun {id: $run_id})
FOREACH (x IN CASE WHEN r IS NULL THEN [] ELSE [r] END | CREATE (x)-[:TRIAGED]->(i))
RETURN i.id AS id, before, prev_seen, prev_count, created, i.triage_count AS triage_count, i.at AS at,
       i.first_seen AS first_seen, CASE WHEN created THEN null ELSE prev_depth END AS prev_depth

// @query crew_set_exposed
// WRITE, atomic. Replace the incident's EXPOSED edges with today's (one per exposed service, so the edge count
// always equals exposed_count). Params: $id, $rows = [{service, path, requirement, pkg, advisory, hops}], $delta (JSON)
MATCH (i:Incident {id: $id})
OPTIONAL MATCH (i)-[e:EXPOSED]->(:Service)
DELETE e
WITH DISTINCT i
SET i.last_delta = $delta
WITH i
UNWIND $rows AS r
MATCH (s:Service {name: r.service})
CREATE (i)-[:EXPOSED {path: r.path, via_requirement: r.requirement, pkg: r.pkg, advisory: r.advisory,
                      hops: r.hops}]->(s)
RETURN count(*) AS exposed_edges

// @query crew_link_entity
// WRITE. Idempotent ABOUT link from the incident to its trigger node. Params: $id, $entity_key
MATCH (i:Incident {id: $id}), (n)
WHERE (n:Maintainer AND n.npm = $entity_key) OR (n:Advisory AND n.id = $entity_key)
   OR (n:Version AND n.key = $entity_key) OR (n:Package AND n.name = $entity_key)
MERGE (i)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query crew_set_patch
// WRITE. Params: $id, $patch (JSON per-service package.json patches)
MATCH (i:Incident {id: $id})
SET i.patch = $patch
RETURN i.id AS id

// @query crew_finish
// WRITE. Params: $id, $status, $finished, $ms, $incident, $message
MATCH (r:CrewRun {id: $id})
SET r.status = $status, r.finished = $finished, r.ms = $ms, r.incident = $incident, r.message = $message
RETURN r.id AS id

// @query crew_get_run
// Read. Params: $id
MATCH (r:CrewRun {id: $id})
OPTIONAL MATCH (r)-[:TRIAGED]->(i:Incident)
RETURN properties(r) AS run, i.id AS incident_id, i.triage_count AS triage_count, i.last_seen AS last_seen,
       i.first_seen AS first_seen, i.last_delta AS last_delta

// @query crew_get_agents
// Read. Params: $id
MATCH (:CrewRun {id: $id})-[:RAN]->(a:AgentRun)
OPTIONAL MATCH (a)-[:PRODUCED]->(f:Finding)
WITH a, f ORDER BY f.seq
WITH a, collect(CASE WHEN f IS NULL THEN NULL ELSE {id: f.id, kind: f.kind, title: f.title, text: f.text,
                                                    data: f.data, at: f.at, seq: f.seq} END) AS findings
RETURN a.id AS id, a.agent AS agent, a.status AS status, a.started AS started, a.finished AS finished, a.ms AS ms,
       a.error AS error, a.summary AS summary, findings
ORDER BY started

// @query crew_get_handoffs
// Read. Params: $id
MATCH (:CrewRun {id: $id})-[:RAN]->(a:AgentRun)-[h:HANDED_OFF_TO]->(b:AgentRun)
RETURN a.agent AS from_agent, b.agent AS to_agent, a.id AS from_run, b.id AS to_run, h.reason AS reason, h.at AS at,
       h.seq AS seq
ORDER BY seq

// @query crew_list_runs
// Read. Newest first. Params: $limit
MATCH (r:CrewRun)
RETURN r.id AS id, r.query AS query, r.depth AS depth, r.status AS status, r.started AS started, r.ms AS ms,
       r.incident AS incident, r.message AS message, r.session_id AS session_id
ORDER BY started DESC LIMIT $limit

// @query crew_count_check
// Read. Integrity check used by tests: nodes and edges written for one run. Params: $id
MATCH (r:CrewRun {id: $id})-[:RAN]->(a:AgentRun)
OPTIONAL MATCH (a)-[:PRODUCED]->(f:Finding)
WITH r, count(DISTINCT a) AS agents, count(f) AS findings
OPTIONAL MATCH (r)-[:RAN]->(:AgentRun)-[h:HANDED_OFF_TO]->(:AgentRun)
WITH agents, findings, count(h) AS handoffs
RETURN agents, findings, handoffs

// @query crew_delete_run
// WRITE. Params: $id. Deletes one run with its agent runs and findings (tests).
MATCH (r:CrewRun {id: $id})
OPTIONAL MATCH (r)-[:RAN]->(a:AgentRun)
OPTIONAL MATCH (a)-[:PRODUCED]->(f:Finding)
DETACH DELETE f, a, r
RETURN count(*) AS deleted
