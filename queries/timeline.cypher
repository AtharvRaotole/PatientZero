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
MATCH (a:Advisory) WHERE a.campaign IS NOT NULL
WITH a.campaign AS campaign, min(a.published) AS at, count(a) AS n, collect(a.pkg + '@' + a.bad_version) AS pkgs
RETURN 'campaign' AS kind, at, campaign AS title, n AS count, pkgs[0..12] AS items, null AS ref
UNION ALL
MATCH (i:Incident)
RETURN 'incident' AS kind, i.at AS at, i.id AS title, i.exposed_count AS count, [i.trigger] AS items, null AS ref
UNION ALL
MATCH (pb:Playbook)
RETURN 'playbook' AS kind, pb.approved_at AS at, coalesce(pb.name, pb.id) AS title, 1 AS count, [pb.id] AS items, null AS ref
