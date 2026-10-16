// Tool: sessions (persistent conversation history, per tenant graph)
// (:Session {id, tenant, created, updated, turns})-[:HAS_TURN]->(:Turn {i, query, resolved_query, intent, outcome,
//            ts, depth, entity_kind, entity_key, message, kind, crew_run, followup_from, exposed_count})
// (:Turn)-[:ABOUT]->(Maintainer | Package | Service | Version | Advisory | Incident | Campaign)
// (:Campaign {id, label, advisories})-[:GROUPS]->(:Advisory {campaign: id})   (created on first mention)
// (:Turn)-[:NEXT]->(:Turn)
// Follow-ups ("who should I wake for that?") read the last Turn and the nodes it is ABOUT from here, never from
// process memory, so a session survives restarts and is visible to every agent and replica.

// @query session_add_turn
// WRITE. Creates the session on first use and appends one turn atomically (FalkorDB serialises writes per graph,
// so concurrent turns in one session still get distinct, gapless i values).
// Params: $sid, $tenant, $ts, $query, $resolved, $intent, $outcome, $depth, $kind_entity, $key_entity, $message,
//         $turn_kind ('ask' | 'crew' | 'fix'), $crew_run, $followup_from, $exposed_count, $qnorm, $epoch
MERGE (s:Session {id: $sid})
ON CREATE SET s.tenant = $tenant, s.created = $ts, s.turns = 0
SET s.updated = $ts, s.turns = s.turns + 1
WITH s
CREATE (s)-[:HAS_TURN]->(t:Turn {i: s.turns, query: $query, resolved_query: $resolved, intent: $intent,
                                outcome: $outcome, ts: $ts, depth: $depth, entity_kind: $kind_entity,
                                entity_key: $key_entity, message: $message, kind: $turn_kind, crew_run: $crew_run,
                                followup_from: $followup_from, exposed_count: $exposed_count, session: $sid,
                                qnorm: $qnorm, epoch: $epoch, kinds: [$turn_kind]})
WITH s, t
OPTIONAL MATCH (s)-[:HAS_TURN]->(p:Turn {i: t.i - 1})
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:NEXT]->(t))
RETURN t.i AS i

// @query session_dedupe_turn
// WRITE. The same question (normalised text, same depth) arriving again in the same session within $window seconds
// (the UI's Replay plus the crew run it starts) is folded into the last turn instead of becoming a duplicate:
// the crew run id is attached and the repeat counted. Returns the turn number, or no row when it is a new turn.
// Params: $sid, $qnorm, $depth, $epoch, $window, $crew_run, $turn_kind, $ts
MATCH (s:Session {id: $sid})-[:HAS_TURN]->(t:Turn)
WHERE t.i = s.turns AND t.qnorm = $qnorm AND coalesce(t.depth, -1) = coalesce($depth, -1)
  AND $epoch - coalesce(t.epoch, 0.0) <= $window
SET t.crew_run = coalesce(t.crew_run, $crew_run), t.repeats = coalesce(t.repeats, 0) + 1,
    t.kinds = CASE WHEN $turn_kind IN coalesce(t.kinds, [t.kind]) THEN coalesce(t.kinds, [t.kind])
                   ELSE coalesce(t.kinds, [t.kind]) + [$turn_kind] END,
    s.updated = $ts
RETURN t.i AS i

// @query session_about_services
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Service {name: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_packages
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Package {name: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_maintainers
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Maintainer {npm: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_versions
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Version {key: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_advisories
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Advisory {id: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_incidents
// WRITE. Params: $sid, $i, $keys
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MATCH (n:Incident {id: k})
MERGE (t)-[:ABOUT]->(n)
RETURN count(n) AS linked

// @query session_about_campaigns
// WRITE. A campaign (e.g. the Shai-Hulud worm) becomes a real node the turn points at, grouping its advisories when
// they are loaded (advisories = 0 when not: the follow-up still resolves and stays honest). Params: $sid, $i,
// $keys (list of {id, label})
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn {i: $i})
UNWIND $keys AS k
MERGE (c:Campaign {id: k.id})
ON CREATE SET c.label = k.label
WITH t, c
OPTIONAL MATCH (a:Advisory {campaign: c.id})
WITH t, c, collect(a) AS advs
FOREACH (a IN advs | MERGE (c)-[:GROUPS]->(a))
SET c.advisories = size(advs)
MERGE (t)-[:ABOUT]->(c)
RETURN count(c) AS linked

// @query session_get
// Read. Params: $sid
MATCH (s:Session {id: $sid})
RETURN s.id AS id, s.tenant AS tenant, s.created AS created, s.updated AS updated, s.turns AS turns

// @query session_turns
// Read. Newest first. Params: $sid, $limit
MATCH (:Session {id: $sid})-[:HAS_TURN]->(t:Turn)
OPTIONAL MATCH (t)-[:ABOUT]->(n)
WITH t, collect({kind: labels(n)[0], key: coalesce(n.npm, n.id, n.key, n.name)}) AS about
RETURN t.i AS i, t.query AS query, t.resolved_query AS resolved_query, t.intent AS intent, t.outcome AS outcome,
       t.ts AS ts, t.depth AS depth, t.entity_kind AS entity_kind, t.entity_key AS entity_key, t.message AS message,
       t.kind AS kind, coalesce(t.kinds, [t.kind]) AS kinds, coalesce(t.repeats, 0) AS repeats, t.crew_run AS crew_run, t.followup_from AS followup_from, t.exposed_count AS exposed_count,
       about
ORDER BY i DESC LIMIT $limit

// @query session_delete
// WRITE. Params: $sid. Used by tests and housekeeping.
MATCH (s:Session {id: $sid})
OPTIONAL MATCH (s)-[:HAS_TURN]->(t:Turn)
DETACH DELETE t, s
RETURN count(*) AS deleted
