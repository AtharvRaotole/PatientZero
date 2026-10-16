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
