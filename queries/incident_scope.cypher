// Tool: incident_scope
// The malicious versions in scope for a compromised maintainer, an advisory id/alias, or a package@version.
// Malicious versions are (:Version {unpublished:true, malicious:true}) nodes linked from (:Advisory).
// Read-only.

// @query incident_scope_by_maintainer
// Params: $maintainer (npm username, e.g. 'qix'). Uses the CURATED HAD_PUBLISH_RIGHTS edge (asOf 2025-09-08),
// not today's registry maintainers.
MATCH (m:Maintainer {npm: $maintainer})-[h:HAD_PUBLISH_RIGHTS]->(p:Package)-[:HAS_VERSION]->(v:Version)<-[:AFFECTS]-(a:Advisory)
RETURN p.name AS pkg, v.version AS bad_version, a.fixed_version AS clean_version, a.id AS advisory,
       a.ghsa AS ghsa, a.cve AS cve, a.aliases AS aliases, a.url AS url, h.asOf AS as_of, h.source AS source
ORDER BY pkg

// @query incident_scope_by_advisory
// Params: $advisory_id (MAL-, GHSA- or CVE- id; aliases accepted)
MATCH (a:Advisory)-[:AFFECTS]->(v:Version)
WHERE toUpper($advisory_id) IN a.ids
RETURN v.pkg AS pkg, v.version AS bad_version, a.fixed_version AS clean_version, a.id AS advisory,
       a.ghsa AS ghsa, a.cve AS cve, a.aliases AS aliases, a.url AS url, a.published AS as_of, a.url AS source

// @query incident_scope_by_version
// Params: $pkg, $version. Returns 0 rows if the version is in the graph but has no advisory.
MATCH (a:Advisory)-[:AFFECTS]->(v:Version {pkg: $pkg, version: $version})
RETURN v.pkg AS pkg, v.version AS bad_version, a.fixed_version AS clean_version, a.id AS advisory,
       a.ghsa AS ghsa, a.cve AS cve, a.aliases AS aliases, a.url AS url, a.published AS as_of, a.url AS source
