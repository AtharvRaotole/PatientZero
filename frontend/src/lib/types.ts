export type Outcome = "answered_with_path" | "answered_from_memory" | "not_in_graph" | "unknown_entity";

export interface TraceStep {
  tool: string;
  query: string | null;
  file?: string | null;
  cypher: string | null;
  params: Record<string, unknown>;
  ms: number;
  rows: number;
  write?: boolean;
  note?: string | null;
  error?: string | null;
  via?: "mcp" | "direct";
  mcp_tool?: string;
  transport?: string;
  tools_listed?: number;
  list_ms?: number;
  intent?: string;
  mcp_fallback?: string;
}

export interface PathInfo {
  via: string[];
  requirements: (string | null)[];
  requirement: string | null;
  hops: number;
  exposed: boolean;
  reason: string;
  display: string;
  resolved_today?: string;
}

export interface Finding {
  package: string;
  bad_version: string;
  clean_version: string | null;
  advisory_id: string | null;
  ghsa?: string | null;
  exposed: boolean;
  path: PathInfo;
  alternatives: PathInfo[];
  path_count: number;
  exposed_path_count?: number;
}

export interface ServiceExposure {
  service: string;
  tier: number | null;
  team: string | null;
  findings: Finding[];
  min_hops: number;
  exposed_packages: string[];
}

export interface Person {
  name: string;
  handle: string;
  email?: string;
  role?: string;
}

export interface WakeEntry {
  service: string;
  tier: number;
  env?: string;
  repo: string;
  team: string;
  team_display: string;
  slack: string;
  pager: string;
  oncall: Person[];
  members: Person[];
}

export interface ScopeRow {
  package: string;
  bad_version: string;
  clean_version: string | null;
  advisory_id: string | null;
  ghsa: string | null;
  cve: string | null;
  url: string | null;
}

export interface FixService {
  service: string;
  tier: number;
  team: string;
  team_display?: string;
  slack?: string;
  oncall: Person[];
  overrides_json: string;
  draft: string;
  paths: string[];
}

export interface Fix {
  per_service: FixService[];
  combined_json: string;
  incident_id: string;
  at: string;
  exposed_edges: number;
  playbooks?: { id: string; name?: string; message: string; agrees_with_proposed_fix: boolean }[];
}

export interface Incident {
  id: string;
  at: string;
  trigger: string;
  depth: number;
  exposed_count: number;
  entity_kind?: string;
  entity_key?: string;
  services?: string[];
  patch?: Record<string, Record<string, string>> | string;
  exposed?: { service: string; path: string[]; requirement: string; advisory: string }[];
}

export interface GraphElem {
  nodes: { id: string; type: string; status?: string; bad?: string }[];
  edges: { id: string; source: string; target: string; requirement: string | null; status: string; last: boolean }[];
}

export interface Spof {
  maintainers: { maintainer: string; reach: number; pagerank: number; packages_in_fleet: number; compromised_in_incident: boolean }[];
  packages: { pkg: string; reach: number }[];
  pagerank: { pkg: string; version: string; score: number }[];
}

export interface AskResponse {
  query: string;
  intent: string;
  outcome: Outcome;
  message: string;
  depth: number;
  trigger: string | null;
  entity_kind: string | null;
  entity_key: string | null;
  exposed_count: number;
  trace: TraceStep[];
  scope: ScopeRow[];
  exposed: ServiceExposure[];
  safe: ServiceExposure[];
  clean: string[];
  fleet_size: number | null;
  wake: WakeEntry[];
  fix: Fix | null;
  memory: { incidents: Incident[]; seen_before?: SeenBefore } | null;
  hypothetical?: boolean;
  spof: Spof | null;
  path: { service: string; package: string; via: string[]; requirements: string[]; hops: number } | null;
  maintainers: { pkg: string; maintainers: string[]; had_publish_rights: string[]; as_of: string[] } | null;
  tenant?: string;
  company?: string;
  /** Present when the question looked like a follow-up (see docs/CONTRACT-crew-sessions-tenants.md). */
  followup?: Followup | null;
  /** A watched advisory where every published version of the package is malicious. */
  all_versions?: AllVersions | null;
  tool_via?: "mcp" | "direct";
  mcp?: McpInfo;
  resolved_query?: string;
  session?: { id: string; turn: number; about_linked: number } | null;
  /** What the router resolved; `campaign` is [key, display name] for named campaigns such as Shai-Hulud. */
  entity?: { services?: string[]; campaign?: [string, string] | null; found?: boolean } | null;
  graph: GraphElem;
  total_ms: number;
  llm_summary: string | null;
  llm_unverified: string[];
  mode: "llm" | "deterministic";
  llm_model: string | null;
}

export interface SummaryResponse {
  summary: string | null;
  unverified: string[];
  model: string | null;
  ms?: number | null;
  error?: string | null;
}

export interface Playbook {
  id: string;
  incident_id?: string;
  name?: string;
  approved_at?: string;
  approved_by?: string;
  trigger?: string;
  overrides?: Record<string, string>;
  services?: string[];
  steps?: string[];
  [k: string]: unknown;
}

export interface ByoItem {
  package?: string;
  bad_version?: string;
  clean_version?: string | null;
  advisory_id?: string | null;
  ghsa?: string | null;
  path?: Partial<PathInfo>;
  via?: string[];
  requirement?: string | null;
  reason?: string;
  hops?: number;
  via_dev_dependency?: boolean;
  runtime_vias?: string[][];
  dev_entries?: string[];
}

export interface ByoVerdict {
  status: "exposed" | "clean" | "not_scanned";
  packages: string[];
  path_count: number;
  text: string;
  via?: string[];
  paths?: { package: string; via: string[]; chain: string }[];
}

export interface ByoScan {
  details?: {
    runtime_hit?: boolean;
    would_have_been_hit?: boolean;
    verdict_runtime?: ByoVerdict;
    verdict_dev?: ByoVerdict;
    payload_note?: string;
  };
  scan_id: string;
  source?: { kind: string; label: string; ref?: string };
  outcome?: Outcome;
  summary: string;
  exposed: ByoItem[];
  not_exposed: ByoItem[];
  overrides?: { overrides: Record<string, string> } | Record<string, string>;
  graph?: GraphElem;
  trace?: TraceStep[];
  stats?: Record<string, number>;
}

export interface FleetGraph {
  nodes: { id: string; kind: "Service" | "Version"; tier: number | null; team: string | null; malicious: boolean }[];
  edges: { source: string; target: string; requirement: string; type: string }[];
}

export interface Health {
  /** True when the server requires X-Admin-Token on reset, cleanup and DELETE. */
  admin_guard?: boolean;
  status: string;
  falkordb: "up" | "down";
  graph: string;
  graph_loaded: boolean;
  error?: string | null;
  agent_mode: "llm" | "deterministic";
  llm_model: string | null;
  queries: number;
}

export interface SeenBefore {
  id: string;
  at: string;
  at_local: string;
  trigger: string | null;
  entity_key: string | null;
  depth: number | null;
  exposed_count: number;
  same_trigger: boolean;
  has_patch: boolean;
  playbooks: string[];
  text: string;
  new_since?: string[];
  no_longer_exposed?: string[];
}

export interface Followup {
  resolved: boolean;
  from_turn?: number;
  anchor?: { kind: string; key: string };
  rewritten?: string;
  depth?: number;
  original?: string;
  reason?: string;
}

export interface SessionTurn {
  i: number;
  query: string;
  resolved_query: string;
  intent: string;
  outcome: string;
  ts: string;
  depth: number;
  entity_kind: string | null;
  entity_key: string | null;
  message: string;
  kind: "ask" | "fix" | "crew";
  crew_run: string | null;
  followup_from: number | null;
  exposed_count: number | null;
  about?: { kind: string; key: string }[];
}

export interface SessionHistory {
  id: string;
  tenant: string;
  created: string;
  updated: string;
  turns: number;
  history: SessionTurn[];
}

export type CrewAgentKey = "triage" | "investigator" | "responder" | "fixer";

export interface CrewFinding {
  id: string;
  agent?: CrewAgentKey;
  kind: string;
  title: string;
  text: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  seq?: number;
  at?: string;
}

export interface CrewAgentRun {
  id: string;
  agent: CrewAgentKey;
  title: string;
  role: string;
  status: string;
  started?: string;
  finished?: string;
  ms: number;
  error: string | null;
  summary: string;
  findings: CrewFinding[];
}

export interface CrewRun {
  run_id: string;
  tenant: string;
  company: string;
  query: string;
  depth: number;
  session_id: string | null;
  status: string;
  started: string;
  finished: string;
  ms: number;
  message: string;
  incident: { id: string; triage_count: number; first_seen: string; last_seen: string; delta: Record<string, unknown> } | null;
  agents: CrewAgentRun[];
  handoffs: { from_agent: CrewAgentKey; to_agent: CrewAgentKey; reason: string; at: string; seq: number }[];
  dag: [CrewAgentKey, CrewAgentKey][];
  session?: { id: string; turn: number; about_linked: number; followup?: Followup | null };
}

// Live OSV watch, MCP and bench (docs/CONTRACT-watch-mcp-bench.md)
export type WatchVerdict = "hit" | "reachable_not_admitted" | "not_hit";
export interface WatchPath { service: string; via: string[]; requirement: string | null; reason?: string }
export interface WatchEvent {
  id: string; ts: string; poll_id: string; tenant: string; advisory: string; aliases?: string[];
  package: string; bad_versions: string[]; all_versions: boolean; verdict: WatchVerdict;
  exposed_services: string[]; exposed_count: number; reachable_services: string[]; fleet_size: number; depth: number;
  replay: boolean; published?: string; summary?: string; url?: string; reason?: string; check_ms?: number; paths?: WatchPath[];
}
export interface WatchPoll {
  id: string; ts: string; source: string; checked: number; new_advisories: number; already_known: number;
  index_entries: number; hits: number; ms: number; errors: number; note?: string; replay?: boolean;
}
export interface WatchLoop { enabled: boolean; interval_s: number; next_at: number | null }
export interface WatchFeed {
  tenant: string; graph: string; events: WatchEvent[]; polls: WatchPoll[]; last_poll: WatchPoll | null;
  counts: { events: number; hits: number; replays: number }; running: boolean; loop: WatchLoop;
}
export interface WatchReplay {
  id: string; replay: true; advisory: string; query: string; checked: number; hits: number; ms: number;
  note: string; record_source?: string; events: WatchEvent[];
}
export interface BenchStats { ops: number; errors: number; throughput_ops_s: number; p50_ms: number; p95_ms: number; p99_ms: number; max_ms: number; mean_ms: number }
export interface BenchRun {
  id: string; label: string; at: string; clients: number; procs?: number; retry_busy?: boolean; duration_s: number;
  read_graph?: string; scratch_deleted?: boolean; overall: BenchStats; reads?: BenchStats; writes?: BenchStats;
  by_op?: Record<string, BenchStats>; events_written?: number; mix?: Record<string, number>;
  falkordb?: { redis_version?: string; modules?: string[] };
}
export interface BenchLast {
  last: BenchRun | null; history: Pick<BenchRun, "id" | "label" | "at" | "clients" | "duration_s" | "overall">[];
  running: unknown; last_error: string | null;
}
export interface McpInfo { via: "mcp" | "direct"; transport?: string; tool: string; intent?: string; args?: Record<string, unknown>; ms?: number; tools_listed?: number; fallback?: string; chosen_by?: string }
export interface AllVersions { packages: string[]; advisories: string[]; lead: string; message: string; services: string[]; fleet_size: number }
