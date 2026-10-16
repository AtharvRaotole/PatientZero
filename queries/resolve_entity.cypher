// Tool: resolve_entity
// Map free-text input to a node in the graph BEFORE any traversal runs.
// Params: $text  raw token, e.g. 'qix', 'GHSA-4x49-vf9v-38px', 'MAL-2025-46974', 'img-service', 'debug'
//         $pkg, $version  parsed from 'pkg@version' input (pass '' when absent)
// Returns 0 rows  => outcome 'unknown_entity' (no further tools run).
// Read-only (ro_query).

// @query resolve_entity
MATCH (m:Maintainer) WHERE toLower(m.npm) = toLower($text)
RETURN 'Maintainer' AS kind, m.npm AS key, m.npm AS label, size([(m)-[:HAD_PUBLISH_RIGHTS]->(x) | x]) > 0 AS in_incident
UNION ALL
MATCH (a:Advisory) WHERE toUpper($text) IN a.ids
RETURN 'Advisory' AS kind, a.id AS key, a.id + ' (' + a.pkg + '@' + a.bad_version + ')' AS label, true AS in_incident
UNION ALL
MATCH (v:Version {pkg: $pkg, version: $version})
RETURN 'Version' AS kind, v.key AS key, v.key AS label, v.malicious AS in_incident
UNION ALL
MATCH (p:Package {name: toLower($text)})
RETURN 'Package' AS kind, p.name AS key, p.name AS label, size([(x:Maintainer)-[:HAD_PUBLISH_RIGHTS]->(p) | x]) > 0 AS in_incident
UNION ALL
MATCH (s:Service {name: toLower($text)})
RETURN 'Service' AS kind, s.name AS key, s.name AS label, false AS in_incident
