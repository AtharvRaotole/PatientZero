// Tool: exposure
// Every dependency path (variable length, up to $depth hops) from a fleet Service to ANY resolved version of
// the affected packages. FalkorDB returns candidate paths; the app then applies npm semver to the LAST edge:
//   exposed  <=> satisfies(bad_version, last_requirement)       (python: nodesemver.satisfies(..., loose=True))
// Rationale: the malicious versions were unpublished, so they never appear in today's resolved trees; a fresh
// install during the attack window would have pulled the bad version iff the declaring range admitted it.
// Params: $pkgs  list of package names (e.g. the 19 from incident_scope, or ['debug'])
//         $depth 1..6. (FalkorDB does not accept a parameter as a var-length bound, so the pattern is fixed
//         at *1..6 and depth is applied with a length(p) filter.)
// Read-only.

// @query exposure
MATCH p = (s:Service)-[:DEPENDS_ON|REQUIRES*1..6]->(v:Version)
WHERE v.pkg IN $pkgs AND length(p) <= $depth
WITH s, p, v, last(relationships(p)) AS r
RETURN s.name AS service, s.tier AS tier, v.pkg AS pkg, v.version AS resolved_today,
       r.requirement AS requirement, length(p) AS hops,
       [n IN nodes(p) | n.name] AS via,
       [e IN relationships(p) | e.requirement] AS requirements
ORDER BY service, hops
