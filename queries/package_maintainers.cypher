// Tool: who_to_wake (package variant, "who owns debug?"). Added by the app layer.
// Today's npm maintainers plus curated historical publish rights. Params: $pkg. Read-only.

// @query package_maintainers
MATCH (p:Package {name: $pkg})
OPTIONAL MATCH (m:Maintainer)-[:MAINTAINS]->(p)
OPTIONAL MATCH (h:Maintainer)-[hp:HAD_PUBLISH_RIGHTS]->(p)
RETURN p.name AS pkg, collect(DISTINCT m.npm) AS maintainers,
       collect(DISTINCT h.npm) AS had_publish_rights, collect(DISTINCT hp.asOf) AS as_of
