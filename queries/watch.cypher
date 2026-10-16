// Tool: watch (live OSV npm malware watch). Owned by backend/app/watch. Every advisory ingested here is a real
// OSV MAL-* record for an npm package; the watcher writes it in the existing schema (Package, Advisory,
// Version {malicious:true, unpublished:true}, AFFECTS) and records one (:WatchEvent) per advisory per tenant.

// @query watch_known_advisories
// Params: $ids. Which advisory ids are already in this graph (so a poll ingests only new ones).
MATCH (a:Advisory) WHERE a.id IN $ids
RETURN a.id AS id

// @query watch_ingest_advisories
// Params: $rows [{id, ids, aliases, ghsa, cve, summary, published, modified, url, pkg, bad_versions, bad_version,
// all_versions, ingested_at}]. MERGE only: an advisory already in the graph (e.g. the curated qix ones) is never
// modified. all_versions advisories (OSV range introduced 0, no fix) link to the Package with AFFECTS_PACKAGE.
UNWIND $rows AS r
MERGE (p:Package {name: r.pkg})
MERGE (a:Advisory {id: r.id})
  ON CREATE SET a.ids = r.ids, a.aliases = r.aliases, a.ghsa = r.ghsa, a.cve = r.cve, a.summary = r.summary,
                a.published = r.published, a.modified = r.modified, a.url = r.url, a.pkg = r.pkg,
                a.bad_version = r.bad_version, a.bad_versions = r.bad_versions, a.all_versions = r.all_versions,
                a.fixed_version = null, a.campaign = 'osv-watch', a.source = 'osv-watch',
                a.ingested_at = r.ingested_at
FOREACH (bv IN r.bad_versions |
  MERGE (v:Version {key: r.pkg + '@' + bv})
    ON CREATE SET v.name = r.pkg + '@' + bv, v.pkg = r.pkg, v.version = bv, v.unpublished = true,
                  v.malicious = true
    ON MATCH SET v.malicious = true
  MERGE (p)-[:HAS_VERSION]->(v)
  MERGE (a)-[:AFFECTS]->(v))
FOREACH (_ IN CASE WHEN r.all_versions THEN [1] ELSE [] END | MERGE (a)-[:AFFECTS_PACKAGE]->(p))
RETURN count(a) AS advisories

// @query watch_fleet_size
MATCH (s:Service) RETURN count(s) AS services

// @query watch_write_events
// Params: $events. One node per (advisory, tenant) check, linked to the advisory it is about.
UNWIND $events AS e
CREATE (w:WatchEvent {id: e.id, ts: e.ts, poll_id: e.poll_id, tenant: e.tenant, advisory: e.advisory,
                      aliases: e.aliases, package: e.package, bad_versions: e.bad_versions,
                      all_versions: e.all_versions, verdict: e.verdict, exposed_services: e.exposed_services,
                      exposed_count: e.exposed_count, reachable_services: e.reachable_services,
                      fleet_size: e.fleet_size, depth: e.depth, replay: e.replay, published: e.published,
                      summary: e.summary, url: e.url, reason: e.reason, check_ms: e.check_ms})
WITH w, e
MATCH (a:Advisory {id: e.advisory})
MERGE (w)-[:ABOUT]->(a)
RETURN count(w) AS linked

// @query watch_link_hits
// Params: $hits [{id, service}]. WatchEvent -[:HITS]-> every exposed Service.
UNWIND $hits AS h
MATCH (w:WatchEvent {id: h.id}), (s:Service {name: h.service})
MERGE (w)-[:HITS]->(s)
RETURN count(*) AS hits

// @query watch_write_poll
// Params: $poll (flat map of numbers and strings).
CREATE (p:WatchPoll {id: $poll.id, ts: $poll.ts, source: $poll.source, checked: $poll.checked,
                     new_advisories: $poll.new_advisories, already_known: $poll.already_known,
                     index_entries: $poll.index_entries, hits: $poll.hits, ms: $poll.ms, errors: $poll.errors,
                     note: $poll.note})
RETURN p.id AS id

// @query watch_feed
// Params: $limit. Newest first.
MATCH (w:WatchEvent)
RETURN properties(w) AS event
ORDER BY w.ts DESC
LIMIT $limit

// @query watch_polls
// Params: $limit. Newest first.
MATCH (p:WatchPoll)
RETURN properties(p) AS poll
ORDER BY p.ts DESC
LIMIT $limit

// @query watch_counts
MATCH (w:WatchEvent)
RETURN count(w) AS events, sum(CASE WHEN w.verdict = 'hit' THEN 1 ELSE 0 END) AS hits,
       sum(CASE WHEN w.replay THEN 1 ELSE 0 END) AS replays

// @query watch_prune_events
// Params: $keep. Keeps the newest $keep WatchEvents.
MATCH (w:WatchEvent)
WITH w ORDER BY w.ts DESC SKIP $keep
DETACH DELETE w
RETURN count(*) AS deleted

// @query watch_prune_polls
// Params: $keep.
MATCH (p:WatchPoll)
WITH p ORDER BY p.ts DESC SKIP $keep
DELETE p
RETURN count(*) AS deleted

// @query watch_replay_lookup
// Params: $q (advisory id or alias, or pkg@version). The advisory record already in the graph.
MATCH (a:Advisory)-[:AFFECTS]->(v:Version)
WHERE toUpper($q) IN a.ids OR v.key = $q
RETURN a.id AS id, a.ids AS ids, a.aliases AS aliases, a.summary AS summary, a.published AS published,
       a.url AS url, v.pkg AS pkg, collect(DISTINCT v.version) AS bad_versions
LIMIT 1

// @query watch_index_event_ts
CREATE INDEX FOR (w:WatchEvent) ON (w.ts)

// @query watch_index_event_id
CREATE INDEX FOR (w:WatchEvent) ON (w.id)
