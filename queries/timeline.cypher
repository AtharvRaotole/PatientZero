// Tool: timeline (incident timeline view). Added by the app layer. Read-only.
// Every dated event the graph holds: the curated compromise (publish rights asOf), advisory publication dates,
// incidents written back by the agent, and playbooks approved by a human.

// @query timeline_events
MATCH (m:Maintainer)-[h:HAD_PUBLISH_RIGHTS]->(p:Package)
WITH h.asOf AS at, m.npm AS who, collect(p.name) AS pkgs, h.source AS source
RETURN 'compromise' AS kind, at, who AS title, size(pkgs) AS count, pkgs[0..25] AS items, source AS ref
UNION ALL
MATCH (a:Advisory) WHERE a.campaign IS NULL
RETURN 'advisory' AS kind, a.published AS at, a.id AS title, 1 AS count, [a.pkg + '@' + a.bad_version] AS items, a.url AS ref
UNION ALL
// Advisories of one incident or campaign (qix: 19, Shai-Hulud worm: 203) collapse into one event.
// Live-watch advisories (campaign 'osv-watch') are not a campaign: they appear below as 'watch' events.
MATCH (a:Advisory) WHERE a.campaign IS NOT NULL AND a.campaign <> 'osv-watch'
WITH a.campaign AS campaign, min(a.published) AS at, count(a) AS n, collect(a.pkg + '@' + a.bad_version) AS pkgs
RETURN 'campaign' AS kind, at, campaign AS title, n AS count, pkgs[0..12] AS items, null AS ref
UNION ALL
MATCH (i:Incident)
RETURN 'incident' AS kind, i.at AS at, i.id AS title, i.exposed_count AS count, [i.trigger] AS items, null AS ref
UNION ALL
MATCH (pb:Playbook)
RETURN 'playbook' AS kind, pb.approved_at AS at, coalesce(pb.name, pb.id) AS title, 1 AS count, [pb.id] AS items, null AS ref
UNION ALL
// Live OSV watch: one event per poll that checked advisories the watch had not seen before, with this tenant's
// verdict. count = advisories checked; items = a sample "package (MAL id): verdict". "First seen by the watch" is
// not "published": the OSV published range is in the title so old advisories are never presented as new.
MATCH (p:WatchPoll) WHERE p.checked > 0
OPTIONAL MATCH (w:WatchEvent {poll_id: p.id})
WITH p, collect(w) AS ws
WITH p, ws, size([w IN ws WHERE w.verdict = 'hit']) AS hits,
     reduce(m = null, x IN [w IN ws | w.published] | CASE WHEN x IS NOT NULL AND (m IS NULL OR x < m) THEN x ELSE m END) AS oldest,
     reduce(m = null, x IN [w IN ws | w.published] | CASE WHEN x IS NOT NULL AND (m IS NULL OR x > m) THEN x ELSE m END) AS newest
RETURN 'watch' AS kind, p.ts AS at,
       CASE WHEN hits > 0 THEN 'hit: ' + toString(hits) + ' of ' + toString(p.checked) + ' advisories first seen by the watch reach this fleet'
            ELSE 'not hit: ' + toString(p.checked) + ' advisories first seen by the watch, none reach this fleet' END +
       CASE WHEN oldest IS NULL THEN '' WHEN substring(oldest, 0, 10) = substring(newest, 0, 10)
            THEN ' (published ' + substring(oldest, 0, 10) + ')'
            ELSE ' (published ' + substring(oldest, 0, 10) + ' to ' + substring(newest, 0, 10) + ')' END AS title,
       p.checked AS count, [w IN ws | w.package + ' (' + w.advisory + '): ' + replace(w.verdict, '_', ' ')][0..8] AS items, null AS ref
UNION ALL
// Replays of past advisories through the watcher: one row per replayed advisory (its latest replay; count = how
// many times it was replayed), latest 3 only. Labelled as replays with the advisory's real publish date.
// One aggregate per WITH on purpose: FalkorDB 6.0.1 mis-projects several aggregates in one grouped WITH.
MATCH (e:WatchEvent) WHERE e.replay = true
WITH e.advisory AS adv, e.package AS pkg, collect(e.ts) AS tss
WITH adv, pkg, size(tss) AS n, reduce(m = '', x IN tss | CASE WHEN x > m THEN x ELSE m END) AS last_ts
MATCH (w:WatchEvent {advisory: adv, package: pkg, ts: last_ts}) WHERE w.replay = true
WITH w, n ORDER BY w.ts DESC LIMIT 3
RETURN 'watch_replay' AS kind, w.ts AS at,
       'replay of ' + w.advisory + ' (' + w.package + ', published ' + coalesce(substring(w.published, 0, 10), 'unknown') + '): ' +
       CASE WHEN w.verdict = 'hit' THEN toString(w.exposed_count) + ' of ' + toString(w.fleet_size) + ' services hit'
            ELSE replace(w.verdict, '_', ' ') END +
       CASE WHEN n > 1 THEN ' (replayed ' + toString(n) + ' times)' ELSE '' END AS title,
       n AS count, [w.package + '@' + coalesce(w.bad_versions[0], '*')] AS items, w.url AS ref
