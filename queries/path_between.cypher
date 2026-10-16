// Tool: path_between
// "Why is <service> connected to <package>?" Shortest dependency chain, with the requirement on every hop.
// Params: $service, $pkg. Read-only.

// @query path_between
// algo.SPpaths (FalkorDB v6.0.1 signature: config map; YIELD path, pathWeight). Edges carry weight=1.
MATCH (s:Service {name: $service}), (v:Version)
WHERE v.pkg = $pkg
CALL algo.SPpaths({sourceNode: s, targetNode: v, relTypes: ['DEPENDS_ON', 'REQUIRES'],
                   maxLen: 8, weightProp: 'weight', pathCount: 1}) YIELD path, pathWeight
RETURN [n IN nodes(path) | n.name] AS via, [e IN relationships(path) | e.requirement] AS requirements,
       pathWeight AS hops
ORDER BY hops LIMIT 1

// @query path_between_shortestpath
// Same answer with the built-in shortestPath() (fallback if algo.SPpaths is unavailable).
MATCH (s:Service {name: $service}), (v:Version)
WHERE v.pkg = $pkg
WITH s, v, shortestPath((s)-[:DEPENDS_ON|REQUIRES*1..8]->(v)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | n.name] AS via, [e IN relationships(p) | e.requirement] AS requirements, length(p) AS hops
ORDER BY hops LIMIT 1
