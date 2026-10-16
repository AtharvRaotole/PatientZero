// Tool: recall
// Prior incidents about an entity (graph memory). Checked BEFORE traversing; a hit => 'answered_from_memory'.
// Params: $entity_key (maintainer npm name, advisory MAL id, version key 'pkg@ver', or package name). Read-only.

// @query recall
// Direct hits, plus incidents about a maintainer whose curated publish rights cover the entity's package
// (so 'debug@4.4.2' or 'GHSA-4x49-vf9v-38px' recalls the 'qix' incident).
MATCH (i:Incident)-[:ABOUT]->(n)
WHERE (n:Maintainer AND n.npm = $entity_key) OR (n:Advisory AND n.id = $entity_key)
   OR (n:Version AND n.key = $entity_key) OR (n:Package AND n.name = $entity_key)
   OR (n:Maintainer AND (n)-[:HAD_PUBLISH_RIGHTS]->(:Package)-[:HAS_VERSION]->(:Version)<-[:AFFECTS]-(:Advisory {id: $entity_key}))
   OR (n:Maintainer AND (n)-[:HAD_PUBLISH_RIGHTS]->(:Package)-[:HAS_VERSION]->(:Version {key: $entity_key}))
   OR (n:Maintainer AND (n)-[:HAD_PUBLISH_RIGHTS]->(:Package {name: $entity_key}))
OPTIONAL MATCH (i)-[e:EXPOSED]->(s:Service)
RETURN i.id AS id, i.at AS at, i.trigger AS trigger, i.depth AS depth, i.exposed_count AS exposed_count,
       i.patch AS patch, labels(n)[0] AS about_kind,
       collect({service: s.name, path: e.path, requirement: e.via_requirement, advisory: e.advisory}) AS exposed
ORDER BY at DESC
