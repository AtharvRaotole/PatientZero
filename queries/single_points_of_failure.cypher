// Tool: single_points_of_failure
// Which maintainer account, if phished, reaches the most fleet services?
//   reach    = distinct Services with a DEPENDS_ON/REQUIRES path (<=6 hops) into any version of a package
//              the maintainer maintains today (MAINTAINS) or had publish rights on (HAD_PUBLISH_RIGHTS, curated)
//   pagerank = sum of algo.pageRank('Version','REQUIRES') over that maintainer's versions (load-bearingness)
// Params: $limit (<=20). Read-only. ~100 ms on the demo fleet.

// @query single_points_of_failure
MATCH (m:Maintainer)-[:MAINTAINS|HAD_PUBLISH_RIGHTS]->(:Package)-[:HAS_VERSION]->(:Version)<-[:DEPENDS_ON|REQUIRES*1..6]-(s:Service)
WITH m, count(DISTINCT s) AS reach ORDER BY reach DESC LIMIT $limit
CALL algo.pageRank('Version', 'REQUIRES') YIELD node, score
WITH m, reach, node, score
MATCH (m)-[:MAINTAINS|HAD_PUBLISH_RIGHTS]->(pk:Package)-[:HAS_VERSION]->(node)
RETURN m.npm AS maintainer, reach, sum(score) AS pagerank, count(DISTINCT pk) AS packages_in_fleet,
       size([(m)-[:HAD_PUBLISH_RIGHTS]->(x) | x]) > 0 AS compromised_in_incident
ORDER BY reach DESC, pagerank DESC

// @query top_packages_pagerank
// Most load-bearing package versions in the fleet dependency graph (algo.pageRank over REQUIRES).
// Params: $limit
CALL algo.pageRank('Version', 'REQUIRES') YIELD node, score
RETURN node.pkg AS pkg, node.version AS version, score
ORDER BY score DESC LIMIT $limit

// @query package_reach
// Fallback / per-package reach count: how many services can reach each package (no algorithm needed).
// Params: $limit
MATCH (s:Service)-[:DEPENDS_ON|REQUIRES*1..6]->(v:Version)
RETURN v.pkg AS pkg, count(DISTINCT s) AS reach
ORDER BY reach DESC LIMIT $limit
