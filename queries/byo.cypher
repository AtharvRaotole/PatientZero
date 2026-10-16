// Tool: byo_scan (bring your own repo). Each scan gets its OWN graph 'byo:<hash>' (FalkorDB multigraph
// tenancy) with the same schema as fleet:acme. The incident data (Advisory, malicious Version, Maintainer with
// curated HAD_PUBLISH_RIGHTS) is copied in from fleet:acme, then the unchanged `exposure` and `incident_scope`
// queries run against the scan graph.

// @query byo_export_incident
// Read from fleet:acme. One row per advisory with its affected version and the curated publish-rights edge.
MATCH (a:Advisory)-[:AFFECTS]->(v:Version)
OPTIONAL MATCH (m:Maintainer)-[h:HAD_PUBLISH_RIGHTS]->(:Package {name: v.pkg})
RETURN properties(a) AS advisory, properties(v) AS version, m.npm AS maintainer, m.name AS maintainer_name,
       m.github AS maintainer_github, h.asOf AS as_of, h.source AS source, h.incident AS incident

// @query byo_index_package
CREATE INDEX FOR (p:Package) ON (p.name)
// @query byo_index_version_key
CREATE INDEX FOR (v:Version) ON (v.key)
// @query byo_index_version_pkg
CREATE INDEX FOR (v:Version) ON (v.pkg)
// @query byo_index_service
CREATE INDEX FOR (s:Service) ON (s.name)
// @query byo_index_advisory
CREATE INDEX FOR (a:Advisory) ON (a.id)
// @query byo_index_maintainer
CREATE INDEX FOR (m:Maintainer) ON (m.npm)

// @query byo_create_scan
// WRITE. Scan metadata (also drives TTL cleanup) + the single Service node for the scanned project.
CREATE (:Scan {id: $id, created_at: $created_at, expires_at: $expires_at, source: $source, repo: $repo,
               ref: $ref, as_of: $as_of, direct_count: $direct_count, has_lockfile: $has_lockfile})
CREATE (:Service {name: $service, tier: 1, env: 'byo', runtime: 'unknown', repo: $repo, byo: true})
RETURN $id AS id

// @query byo_create_packages
// WRITE. Params: $rows = [{name}]
UNWIND $rows AS r
CREATE (:Package {name: r.name})
RETURN count(*) AS packages

// @query byo_create_versions
// WRITE. Params: $rows = [{key, pkg, version, unpublished, malicious}]
UNWIND $rows AS r
MATCH (p:Package {name: r.pkg})
CREATE (p)-[:HAS_VERSION]->(:Version {key: r.key, name: r.key, pkg: r.pkg, version: r.version,
                                      unpublished: r.unpublished, malicious: r.malicious})
RETURN count(*) AS versions

// @query byo_create_depends_on
// WRITE. Direct dependencies of the scanned project. Params: $service, $rows = [{key, requirement, dev}]
UNWIND $rows AS r
MATCH (s:Service {name: $service}), (v:Version {key: r.key})
CREATE (s)-[:DEPENDS_ON {requirement: r.requirement, dev: r.dev, weight: 1}]->(v)
RETURN count(*) AS edges

// @query byo_create_requires
// WRITE. deps.dev resolved edges, declared range kept. Params: $rows = [{a, b, req}]
UNWIND $rows AS r
MATCH (a:Version {key: r.a}), (b:Version {key: r.b})
CREATE (a)-[:REQUIRES {requirement: r.req, weight: 1}]->(b)
RETURN count(*) AS edges

// @query byo_create_advisories
// WRITE. Copied incident data. Params: $rows = [advisory properties + key]
UNWIND $rows AS r
MATCH (v:Version {key: r.key})
CREATE (a:Advisory {id: r.id, ids: r.ids, aliases: r.aliases, ghsa: r.ghsa, cve: r.cve, summary: r.summary,
                    published: r.published, url: r.url, pkg: r.pkg, bad_version: r.bad_version,
                    fixed_version: r.fixed_version})-[:AFFECTS]->(v)
RETURN count(a) AS advisories

// @query byo_create_publish_rights
// WRITE. Params: $rows = [{npm, name, github, pkg, as_of, source, incident}]
UNWIND $rows AS r
MERGE (m:Maintainer {npm: r.npm})
SET m.name = r.name, m.github = r.github
WITH m, r
MATCH (p:Package {name: r.pkg})
CREATE (m)-[:HAD_PUBLISH_RIGHTS {asOf: r.as_of, source: r.source, incident: r.incident}]->(p)
RETURN count(*) AS edges

// @query byo_scan_meta
// Read. Scan metadata for TTL cleanup and GET /api/byo/scans.
MATCH (s:Scan)
OPTIONAL MATCH (v:Version)
RETURN properties(s) AS scan, count(v) AS versions

// @query byo_graph_stats
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label
