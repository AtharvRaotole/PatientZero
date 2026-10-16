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
// Live OSV watch: one event per poll that checked new advisories, with this tenant's verdict. count = advisories
// checked; items = a sample "package (MAL id): verdict".
MATCH (p:WatchPoll) WHERE p.checked > 0
OPTIONAL MATCH (w:WatchEvent {poll_id: p.id})
WITH p, collect(w) AS ws
WITH p, ws, size([w IN ws WHERE w.verdict = 'hit']) AS hits
RETURN 'watch' AS kind, p.ts AS at,
       CASE WHEN hits > 0 THEN 'hit: ' + toString(hits) + ' of ' + toString(p.checked) + ' new advisories reach this fleet'
            ELSE 'not hit: ' + toString(p.checked) + ' new advisories checked, none reach this fleet' END AS title,
       p.checked AS count, [w IN ws | w.package + ' (' + w.advisory + '): ' + replace(w.verdict, '_', ' ')][0..8] AS items, null AS ref
UNION ALL
// Replays of past advisories through the watcher (labelled, latest 3 only).
MATCH (w:WatchEvent) WHERE w.replay = true
WITH w ORDER BY w.ts DESC LIMIT 3
RETURN 'watch_replay' AS kind, w.ts AS at,
       'replay of ' + w.advisory + ' (' + w.package + '): ' +
       CASE WHEN w.verdict = 'hit' THEN toString(w.exposed_count) + ' of ' + toString(w.fleet_size) + ' services hit'
            ELSE replace(w.verdict, '_', ' ') END AS title,
       1 AS count, [w.package + '@' + coalesce(w.bad_versions[0], '*')] AS items, w.url AS ref
