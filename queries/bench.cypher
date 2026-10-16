// Tool: bench (concurrency benchmark). Owned by backend/app/bench. Reads run on a tenant fleet graph; writes go
// to a throwaway graph 'bench:<run id>' that is deleted when the run ends.

// @query bench_setup
// Params: $run. One anchor node per run in the scratch graph.
MERGE (r:BenchRun {id: $run})
RETURN r.id AS id

// @query bench_write_event
// Params: $run, $id, $client, $ts, $service. A WatchEvent-shaped write linked to its run.
MATCH (r:BenchRun {id: $run})
CREATE (e:BenchEvent {id: $id, client: $client, ts: $ts, service: $service, verdict: 'not_hit'})-[:IN_RUN]->(r)
RETURN e.id AS id

// @query bench_read_scratch
// Params: $run. Read-after-write on the scratch graph.
MATCH (:BenchRun {id: $run})<-[:IN_RUN]-(e:BenchEvent)
RETURN count(e) AS events

// @query bench_count
MATCH (e:BenchEvent) RETURN count(e) AS events
