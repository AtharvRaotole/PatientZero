"use client";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Info, Loader2, Play, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, ApiError, adminToken, apiGet, apiPost } from "@/lib/api";
import type { AskResponse, FleetGraph, GraphElem, Health, Incident, Playbook } from "@/lib/types";
import { NotHitView, ResponderView, isNotHit } from "@/components/answers";
import { CrewView, type CrewSpec } from "@/components/crew";
import { BenchPanel, LiveWatch } from "@/components/watch";
import { SessionRail, TenantSwitcher } from "@/components/session";
import { getTenant, initTenant, markSessionUsed, newSessionId, sessionUsed, sessionIdFor, setTenant as storeTenant, type TenantInfo } from "@/lib/tenant";
import type { SessionHistory, SessionTurn } from "@/lib/types";
import {
  ErrorCard, ExposurePanel, FixPanel, InsightsPanel, MemoryDetail, MemoryPanel, OutcomeBanner, PathPanel, ScopeStrip, SpofPanel,
  TracePanel, WakeCard, WakeOnly,
} from "@/components/panels";
import { Card, CountUp, ReportSection, Segmented, Skeleton, Status } from "@/components/ui";
import { AgentAnswer, PlaybooksPanel } from "@/components/agentui";
import ByoSection from "@/components/ByoSection";
import { Timeline, type TimelineEvent } from "@/components/viz";
import { SeenBefore } from "@/components/SeenBefore";
import { Logo } from "@/components/Logo";
import { CommandBar, type Suggestion } from "@/components/CommandBar";
import { QIX_DOWNLOADS_SOURCE, QIX_PACKAGES, QIX_WEEK_DOWNLOADS } from "@/lib/facts";

const REPLAY = "maintainer qix was phished";
const GLOBEX_REPLAY = "Shai-Hulud";
const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });

const PRESETS: Suggestion[] = [
  { q: "maintainer qix was phished", hint: "Replay the Sept 8, 2025 compromise", group: "Incidents" },
  { q: "debug@4.4.2", hint: "One malicious version, with honest negatives", group: "Incidents" },
  { q: "GHSA-4x49-vf9v-38px", hint: "Start from an advisory id", group: "Incidents" },
  { q: "who is our single point of failure?", hint: "Maintainer reach and algo.pageRank", group: "Fleet" },
  { q: "why is img-service connected to is-arrayish?", hint: "Shortest path with algo.SPpaths", group: "Fleet" },
  { q: "who do I wake for checkout-api", hint: "Owning team and on-call", group: "Fleet" },
  { q: "ansi-regex@6.2.1", hint: "Reached, but every range excludes it", group: "Edge cases" },
  { q: "left-pad@1.3.0", hint: "Not in the graph, so nothing is invented", group: "Edge cases" },
];

const GLOBEX_PRESETS: Suggestion[] = [
  { q: "Shai-Hulud", hint: "The Sept 15, 2025 worm: Globex is hit", group: "Incidents" },
  { q: "maintainer qix was phished", hint: "Replay the Sept 8, 2025 compromise", group: "Incidents" },
  { q: "who should I wake for that?", hint: "A follow-up, resolved against this session", group: "Follow-ups" },
  { q: "and at depth 6?", hint: "Rerun the last question deeper", group: "Follow-ups" },
  { q: "who is our single point of failure?", hint: "Maintainer reach and algo.pageRank", group: "Fleet" },
];
const FOLLOWUP_PRESETS: Suggestion[] = [
  { q: "who should I wake for that?", hint: "A follow-up, resolved against this session", group: "Follow-ups" },
  { q: "and at depth 6?", hint: "Rerun the last question deeper", group: "Follow-ups" },
];
const presetsFor = (t: string) => (t === "globex" ? GLOBEX_PRESETS : [...PRESETS, ...FOLLOWUP_PRESETS]);
const wantsWake = (q: string) => /\b(wake|page|on-?call|who should)\b/i.test(q);

type Err = { message: string; kind: string };

function sessionId() {
  if (typeof window === "undefined") return "";
  let s = sessionStorage.getItem("pz-session");
  if (!s) {
    s = (crypto.randomUUID?.() ?? String(Math.random())).slice(0, 8);
    sessionStorage.setItem("pz-session", s);
  }
  return s;
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [fleet, setFleet] = useState<FleetGraph | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [query, setQuery] = useState("");
  const [lastQuery, setLastQuery] = useState("");
  const [depth, setDepthState] = useState(4);
  // The slider value as a ref too, so an answer can be checked against what the slider shows right now.
  const depthRef = useRef(4);
  const setDepth = useCallback((d: number) => { depthRef.current = d; setDepthState(d); }, []);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fixLoading, setFixLoading] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const [mode, setMode] = useState<"auto" | "deterministic">("auto");
  const [playbooks, setPlaybooks] = useState<Playbook[] | null>(null);
  const [pbErr, setPbErr] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [tenant, setTenantState] = useState("acme");
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [svcByTenant, setSvcByTenant] = useState<Record<string, number>>({});
  const [history, setHistory] = useState<SessionHistory | null>(null);
  const [histErr, setHistErr] = useState<string | null>(null);
  const [crew, setCrew] = useState<CrewSpec | null>(null);
  const crewRef = useRef<HTMLDivElement>(null);
  const fleetCache = useRef<Record<string, FleetGraph>>({});

  const refreshTimeline = useCallback(async () => {
    try {
      setTimeline((await apiGet<{ events: TimelineEvent[] }>("/api/timeline")).events);
    } catch {
      setTimeline([]);
    }
  }, []);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<HTMLElement>(null);

  const loadMeta = useCallback(async () => {
    try {
      const h = await apiGet<Health>("/health");
      setHealth(h);
      setHealthErr(null);
      if (h.falkordb === "up" && h.graph_loaded) {
        const t = getTenant();
        const cached = fleetCache.current[t];
        if (cached) setFleet(cached);
        const [g, inc] = await Promise.all([
          cached ? Promise.resolve(cached) : apiGet<FleetGraph>("/api/fleet/graph"),
          apiGet<{ incidents: Incident[] }>("/api/incidents"),
        ]);
        if (t !== getTenant()) return; // switched again while loading
        fleetCache.current[t] = g;
        setFleet(g);
        setSvcByTenant((m) => ({ ...m, [t]: g.nodes.filter((n) => n.kind === "Service").length }));
        setIncidents(inc.incidents);
      }
    } catch (e) {
      setHealth(null);
      setHealthErr((e as Error).message);
    }
  }, []);

  // Tenant: read once from the URL or localStorage, then everything below reloads per tenant.
  useEffect(() => {
    setTenantState(initTenant());
    apiGet<{ tenants: TenantInfo[] }>("/api/tenants").then((r) => setTenants(r.tenants), () => setTenants([]));
    const run = new URL(window.location.href).searchParams.get("run");
    if (run) setCrew({ key: Date.now(), mode: "replay", runId: run });
  }, []);

  // Only the newest history request may land; a slower, older response is dropped. A fetched history never
  // replaces a longer one for the same session (the optimistic turn from the answer is already on screen).
  const histSeq = useRef(0);
  const refreshHistory = useCallback(async () => {
    const my = ++histSeq.current;
    const t = getTenant();
    const id = sessionIdFor(t);
    if (!sessionUsed()) { setHistory(null); setHistErr(null); return; }
    try {
      const h = await apiGet<SessionHistory>(`/api/sessions/${encodeURIComponent(id)}?limit=50`, t);
      if (my !== histSeq.current || t !== getTenant() || id !== sessionIdFor(t)) return;
      setHistory((cur) => (cur && cur.id === h.id && cur.history.length > h.history.length ? cur : h));
      setHistErr(null);
    } catch (e) {
      if (my !== histSeq.current) return;
      setHistory(null);
      setHistErr((e as ApiError).status === 404 ? null : (e as Error).message);
    }
  }, []);
  const appendTurn = useCallback((t: string, text: string, r: AskResponse) => {
    if (!r.session?.id || !r.session.turn) return;
    const turn: SessionTurn = {
      i: r.session.turn, query: text, resolved_query: r.resolved_query ?? text, intent: r.intent ?? "", outcome: r.outcome,
      ts: new Date().toISOString(), depth: r.depth, entity_kind: r.entity_kind, entity_key: null, message: r.message,
      kind: "ask", crew_run: null, followup_from: r.followup?.resolved ? r.followup.from_turn ?? null : null,
      exposed_count: r.exposed.length,
    };
    histSeq.current++; // anything fetched before this answer is older than it
    setHistory((cur) => {
      if (t !== getTenant()) return cur;
      const base: SessionHistory = cur && cur.id === r.session!.id ? cur
        : { id: r.session!.id, tenant: t, created: turn.ts, updated: turn.ts, turns: 0, history: [] };
      const rest = base.history.filter((x) => x.i !== turn.i);
      const history = [...rest, turn].sort((a, b) => a.i - b.i);
      return { ...base, updated: turn.ts, turns: Math.max(base.turns, history.length), history };
    });
    setHistErr(null);
  }, []);

  useEffect(() => {
    loadMeta();
    refreshHistory();
  }, [loadMeta, refreshHistory, tenant]);

  // Warm the other tenants' fleet graphs so a switch paints instantly.
  useEffect(() => {
    if (!tenants.length) return;
    const t = setTimeout(() => {
      tenants.filter((x) => x.loaded && !fleetCache.current[x.id]).forEach((x) => {
        fetch(`${API_URL}/api/fleet/graph?tenant=${x.id}`, { headers: { "X-Tenant": x.id } }).then((r) => (r.ok ? r.json() : null)).then((g: FleetGraph | null) => {
          if (!g) return;
          fleetCache.current[x.id] = g;
          setSvcByTenant((m) => ({ ...m, [x.id]: g.nodes.filter((n) => n.kind === "Service").length }));
        }, () => {});
      });
    }, 1200);
    return () => clearTimeout(t);
  }, [tenants]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && document.activeElement?.tagName !== "INPUT")) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const refreshPlaybooks = useCallback(async () => {
    try {
      const r = await apiGet<{ playbooks: Playbook[] }>("/api/playbooks");
      setPlaybooks(r.playbooks ?? []);
      setPbErr(null);
    } catch (e) {
      const a = e as ApiError;
      setPbErr(a.status === 404 ? "Playbooks are not enabled on this backend yet." : a.message);
    }
  }, []);

  useEffect(() => {
    refreshPlaybooks();
    refreshTimeline();
  }, [refreshPlaybooks, refreshTimeline, tenant]);

  const refreshIncidents = useCallback(async () => {
    try {
      const inc = await apiGet<{ incidents: Incident[] }>("/api/incidents");
      setIncidents(inc.incidents);
    } catch {
      /* memory list is secondary; ignore */
    }
  }, []);

  // Only the latest request may land: each query or depth change aborts the one in flight, and an answer whose
  // depth is not what the slider shows now is dropped. A follow-up like "and at depth 6?" is the one case where
  // the answer moves the slider, and only when it is the latest request.
  const seq = useRef(0);
  const depthTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const run = useCallback(
    async (q: string, opts: { depth?: number; fresh?: boolean } = {}) => {
      const text = q.trim();
      if (!text) return;
      const my = ++seq.current;
      inflight.current?.abort();
      const ctl = new AbortController();
      inflight.current = ctl;
      const asked = opts.depth ?? depthRef.current;
      setQuery(text);
      setLastQuery(text);
      setLoading(true);
      setErr(null);
      try {
        const t = getTenant();
        const r = await apiPost<AskResponse>("/api/ask", { query: text, depth: asked, fresh: !!opts.fresh, session: sessionId(), mode, tenant: t, session_id: sessionIdFor(t) }, ctl.signal);
        if (my !== seq.current || ctl.signal.aborted) return;
        if (asked !== depthRef.current) {
          // The slider moved while this was in flight: this answer is for an old depth, never show it.
          if (!depthTimer.current) run(text, { depth: depthRef.current, fresh: opts.fresh });
          return;
        }
        // The answer's depth wins only when it differs from what was asked ("and at depth 6?"), so slider and answer agree.
        if (r.depth && r.depth !== asked) setDepth(r.depth);
        setResult(r);
        markSessionUsed(t);
        appendTurn(t, text, r);
        refreshHistory();
        // Incidents draw on the fleet graph; every other answer (who to wake, not hit, path, SPOF) is read in the report.
        const toGraph = r.exposed.length > 0 || r.safe.length > 0;
        const wakeSection = toGraph && wantsWake(text) && r.wake.length > 0;
        setTimeout(() => {
          const el = wakeSection ? document.getElementById("who-to-wake") : toGraph ? graphRef.current : resultsRef.current;
          el?.scrollIntoView({ behavior: "smooth", block: "start" });
          if (!toGraph) resultsRef.current?.focus({ preventScroll: true });
        }, 120);
      } catch (e) {
        if (my !== seq.current || ctl.signal.aborted) return;
        const a = e as ApiError;
        setErr({ message: a.message, kind: a.kind ?? "error" });
        setResult(null);
        if (a.kind === "graph_unavailable" || a.kind === "network") loadMeta();
      } finally {
        if (my === seq.current) setLoading(false);
      }
    },
    [loadMeta, mode, refreshHistory, appendTurn, setDepth],
  );

  const switchTenant = useCallback((t: string) => {
    if (t === getTenant()) return;
    seq.current++; // drop any answer still in flight for the old tenant
    inflight.current?.abort();
    histSeq.current++; // and any history request
    storeTenant(t);
    setTenantState(t);
    setResult(null);
    setErr(null);
    setQuery("");
    setLastQuery("");
    setCrew(null);
    setHistory(null);
    setIncidents(null);
    setFleet(fleetCache.current[t] ?? null);
    const url = new URL(window.location.href);
    if (url.searchParams.has("run")) { url.searchParams.delete("run"); window.history.replaceState(null, "", url.toString()); }
  }, []);

  // The response crew: four agents over SSE, opened under the hero and replayable from ?run=.
  const startCrew = useCallback((q: string) => {
    const text = q.trim();
    if (!text) return;
    setCrew({ key: Date.now(), mode: "live", query: text, depth, sessionId: sessionIdFor() });
    setTimeout(() => crewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  }, [depth]);
  const onCrewRunId = useCallback((id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("run", id);
    window.history.replaceState(null, "", url.toString());
  }, []);
  const closeCrew = useCallback(() => {
    setCrew(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    window.history.replaceState(null, "", url.toString());
  }, []);
  const onCrewFinished = useCallback(() => {
    markSessionUsed();
    refreshHistory();
    refreshIncidents();
    refreshTimeline();
    refreshPlaybooks();
  }, [refreshHistory, refreshIncidents, refreshTimeline, refreshPlaybooks]);
  const onNewSession = useCallback(() => {
    newSessionId();
    histSeq.current++;
    setHistory(null);
    setHistErr(null);
  }, []);

  // Dragging the slider re-traces once it settles, not once per step.
  const onDepth = (d: number) => {
    setDepth(d);
    if (!lastQuery) return;
    if (depthTimer.current) clearTimeout(depthTimer.current);
    seq.current++; // an answer in flight is for an older depth now
    inflight.current?.abort();
    setLoading(true);
    depthTimer.current = setTimeout(() => { depthTimer.current = null; run(lastQuery, { depth: d, fresh: true }); }, 220);
  };

  const onFix = async () => {
    if (!lastQuery) return;
    setFixLoading(true);
    try {
      const r = await apiPost<AskResponse>("/api/fix", { query: lastQuery, depth, session: sessionId(), tenant: getTenant(), session_id: sessionIdFor() });
      setResult(r);
      refreshIncidents();
      refreshTimeline();
    } catch (e) {
      const a = e as ApiError;
      setErr({ message: a.message, kind: a.kind ?? "error" });
    } finally {
      setFixLoading(false);
    }
  };

  // Reset is admin-only when the server guards it (live site); locally the guard is off and the button always shows.
  const [admin, setAdmin] = useState<string | null>(null);
  useEffect(() => setAdmin(adminToken()), []);
  const canReset = !!health && (!health.admin_guard || !!admin);

  const onReset = async () => {
    try {
      await apiPost("/api/memory/reset", {});
      refreshIncidents();
    } catch (e) {
      setErr({ message: (e as Error).message, kind: "error" });
    }
  };

  const up = health?.falkordb === "up" && health.graph_loaded;
  const svcCount = fleet?.nodes.filter((n) => n.kind === "Service").length ?? 0;
  const pkgCount = fleet ? fleet.nodes.length - svcCount : 0;
  const isIncident = result && (result.exposed.length > 0 || result.safe.length > 0);
  // What the fleet graph emphasizes. Answers without their own subgraph still say something on it:
  // a clean check paints every service clean, who-to-wake marks the service asked about.
  const graphHl = useMemo<GraphElem | null>(() => {
    if (!result) return null;
    if (result.graph.nodes.length) return result.graph;
    if (isNotHit(result)) return { nodes: result.clean.map((id) => ({ id, type: "service", status: "clean" })), edges: [] };
    if (result.wake.length) return { nodes: result.wake.map((w) => ({ id: w.service, type: "service", status: "focus" })), edges: [] };
    return result.graph;
  }, [result]);


  return (
    <div className="min-h-screen bg-paper text-ink">
      {/* Top bar */}
      <header className="sticky top-0 z-30 border-b border-line bg-paper">
        <div className="mx-auto flex h-14 max-w-[1240px] items-center justify-between gap-4 px-6">
          <div className="flex items-center gap-5">
            <Logo />
            <span className="hidden h-4 w-px bg-line md:block" />
            {tenants.length > 1 && <TenantSwitcher tenants={tenants} value={tenant} onChange={switchTenant} services={svcByTenant} />}
            {health ? (
              <Status tone={up ? "safe" : "danger"}>
                {up ? <>Connected to FalkorDB <motion.span key={health.graph} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }} data-testid="graph-name" className="hidden font-mono text-[12px] text-ink-3 md:inline">{health.graph}</motion.span></> : `FalkorDB ${health.falkordb}`}
              </Status>
            ) : healthErr ? <Status tone="danger">API offline</Status> : <Skeleton className="h-4 w-36" />}
          </div>
          <nav className="flex items-center gap-2">
            <a href="#byo" className="press hidden h-8 items-center rounded-lg px-3 text-[13px] font-medium text-ink-2 hover:bg-subtle hover:text-ink md:inline-flex">Scan your repo</a>
            {health?.agent_mode === "llm" ? (
              <span className="group relative flex items-center gap-1.5">
                <Segmented size="sm" value={mode} onChange={setMode} options={[{ value: "auto", label: "Auto" }, { value: "deterministic", label: "Deterministic" }]} />
                <button type="button" aria-label="What Auto and Deterministic mean" className="grid h-7 w-7 place-items-center rounded-md text-ink-3 hover:bg-subtle hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-line-strong">
                  <Info className="h-3.5 w-3.5" />
                </button>
                <span role="tooltip" className="pointer-events-none invisible absolute right-0 top-[calc(100%+8px)] z-40 w-[300px] rounded-lg border border-line bg-surface p-3 text-[12.5px] leading-relaxed text-ink-2 opacity-0 shadow-md transition-opacity duration-150 group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100">
                  <b className="font-medium text-ink">Auto:</b> the rule router answers first. When it is unsure, GPT picks a tool, and every name it uses is verified against the graph.
                  <span className="mt-1.5 block"><b className="font-medium text-ink">Deterministic:</b> rule router only, no LLM.</span>
                </span>
              </span>
            ) : health ? <span className="text-[12px] text-ink-3">Deterministic</span> : null}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1240px] px-6 pb-24">
        {/* Hero */}
        <section className="grid gap-6 pb-6 pt-8 md:pt-10 xl:grid-cols-[minmax(0,1fr)_300px] xl:items-end">
          <div className="min-w-0">
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
            <motion.p key={tenant} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }} className="flex items-center gap-2 text-[13px] text-ink-2" title={tenant === "globex" ? undefined : `Source: ${QIX_DOWNLOADS_SOURCE}`}>
              <span className="h-1.5 w-1.5 rounded-full bg-signal" />
              {tenant === "globex"
                ? <>Sept 2025. Shai-Hulud, a self-replicating npm worm. Try it on the Globex fleet.</>
                : <>Sept 8, 2025. One phished maintainer, {QIX_PACKAGES} packages, {(QIX_WEEK_DOWNLOADS / 1e9).toFixed(2)} billion downloads the week before.</>}
            </motion.p>
            <h1 className="mt-4 text-[34px] font-semibold leading-[1.1] tracking-[-0.025em] text-ink md:text-[44px]">
              Know your blast radius <span className="font-serif text-[1.08em] font-normal italic tracking-[-0.01em] text-ink-2">before the postmortem.</span>
            </h1>
            <p className="mt-3 max-w-[620px] text-[16px] leading-relaxed text-ink-2">
              PatientZero walks every dependency chain in your fleet, checks the npm semver range on the last edge, and tells you who to wake and what to pin.
            </p>
          </motion.div>
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay: 0.08 }} className="mt-6 max-w-[760px]">
            <CommandBar inputRef={inputRef} value={query} onChange={setQuery} suggestions={presetsFor(tenant)} disabled={!up} loading={loading}
              onRun={(q, fromSuggestion) => run(q, { fresh: fromSuggestion && q.includes("phished") })} />
            <Interpreted result={loading ? null : result} asked={lastQuery} />
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
              <button onClick={() => run(tenant === "globex" ? GLOBEX_REPLAY : REPLAY, { fresh: true })} disabled={!up || loading} data-testid="hero-replay"
                className="press inline-flex h-9 items-center gap-2 rounded-lg bg-ink px-3.5 text-[13.5px] font-medium text-paper transition-colors hover:bg-ink/85 disabled:opacity-40">
                <Play className="h-3.5 w-3.5 fill-current" /> {tenant === "globex" ? "Replay Shai-Hulud on Globex" : "Replay the qix compromise"}
              </button>
              <button onClick={() => startCrew(tenant === "globex" ? "Shai-Hulud" : REPLAY)} disabled={!up} data-testid="hero-crew"
                className="press inline-flex h-9 items-center gap-2 rounded-lg bg-surface px-3.5 text-[13.5px] font-medium text-ink ring-1 ring-inset ring-line-strong transition-colors hover:bg-subtle disabled:opacity-40">
                <Users className="h-3.5 w-3.5" /> Run the response crew
              </button>
              <LastTraversal result={result} loading={loading} queries={health?.queries} />
            </div>
          </motion.div>
          </div>
          {up && <SessionRail history={history} error={histErr} onPick={(q) => run(q)} onNew={onNewSession} busy={loading} />}
        </section>

        {crew && (
          <div ref={crewRef} className="scroll-mt-[72px] pb-8">
            <CrewView key={crew.key} spec={crew} onClose={closeCrew} onRunId={onCrewRunId} onFinished={onCrewFinished}
              onRestart={() => crew.mode === "live" ? startCrew(crew.query) : setCrew({ ...crew, key: Date.now() })} />
          </div>
        )}

        {/* Graph */}
        <motion.section ref={graphRef} id="graph" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.12 }}
          className="scroll-mt-[72px] overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5">
            <div className="flex items-center gap-4 text-[13px] text-ink-2">
              <span className="font-medium text-ink">Fleet graph</span>
              {fleet && (
                <span className="hidden gap-4 sm:flex">
                  <span><CountUp value={svcCount} className="text-ink" /> services</span>
                  <span><CountUp value={pkgCount} duration={1.2} className="text-ink" /> package versions</span>
                  <span><CountUp value={fleet.edges.length} duration={1.2} className="text-ink" /> edges</span>
                </span>
              )}
            </div>
            <div className="flex items-center gap-4">
              <div className="hidden items-center gap-3.5 text-[12px] text-ink-2 lg:flex">
                <Legend kind="service" label="Service" />
                <Legend kind="version" label="Package version" />
                <Legend kind="exposed" label="Exposed" />
                <Legend kind="safe" label="Excluded by range" />
              </div>
              {isIncident && (
                <label className="flex items-center gap-2.5 border-l border-line pl-4 text-[12px] text-ink-2">
                  Depth
                  <input aria-label="graph depth" type="range" min={1} max={6} value={depth} onChange={(e) => onDepth(Number(e.target.value))} className="depth w-24" />
                  <span className="w-3 font-mono text-[13px] text-ink tabular">{depth}</span>
                </label>
              )}
            </div>
          </div>
          <div className="relative h-[460px] md:h-[540px]">
            {fleet ? (
              <GraphCanvas fleet={fleet} highlight={graphHl} onNodeClick={() => {}} leftInset={0} topInset={0} />
            ) : (
              <div className="absolute inset-0 grid place-items-center">
                {healthErr || (health && !up) ? <p className="text-[13px] text-ink-3">The graph is unavailable.</p> : (
                  <div className="flex items-center gap-2 text-[13px] text-ink-3"><Loader2 className="h-4 w-4 animate-spin" /> Loading the fleet graph from FalkorDB</div>
                )}
              </div>
            )}
            <AnimatePresence>
              {loading && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
                  className="pointer-events-none absolute left-1/2 top-4 -translate-x-1/2">
                  <div className="flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] text-ink-2 shadow-sm">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Traversing the graph
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.section>

        {/* Report */}
        <div ref={resultsRef} id="report" tabIndex={-1} className="scroll-mt-20 pt-10 outline-none">
          {healthErr && <ErrorCard kind="network" message={`${healthErr}. Start the backend: cd backend && uv run uvicorn app.main:app --port 8000`} onRetry={loadMeta} />}
          {health && !up && !healthErr && (
            <ErrorCard kind="graph_unavailable" message={health.falkordb === "down" ? "Graph unavailable: FalkorDB is not reachable. Run scripts/start-falkordb.sh. PatientZero never answers from anywhere else." : `Graph ${health.graph} is empty. Load it with ingest/load.py.`} onRetry={loadMeta} />
          )}
          {err && <div className="mb-4"><ErrorCard kind={err.kind} message={err.message} onRetry={() => (lastQuery ? run(lastQuery) : loadMeta())} /></div>}

          {!result && !loading && !err && up && <EmptyState tenant={tenant} onPick={(q) => run(q, { fresh: q.includes("phished") || q === "Shai-Hulud" })} />}

          {loading && !result && <ReportSkeleton />}

          {result && (
            <div className={loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
              <OutcomeBanner r={result} onFresh={() => run(lastQuery, { fresh: true })} onCrew={isIncident && result.exposed.length > 0 ? () => startCrew(result.resolved_query ?? lastQuery) : undefined} />
              {result.memory?.seen_before && result.outcome !== "answered_from_memory" && <div className="mt-4"><SeenBefore sb={result.memory.seen_before} /></div>}
              <div className="mt-4"><AgentAnswer key={result.query + result.total_ms} r={result} llmAvailable={health?.agent_mode === "llm"} /></div>

              {isIncident ? (
                <div className="mt-8">
                  <ReportSection n={1} title="Scope" sub="Malicious versions in this incident and the clean version to pin." delay={0.02}>
                    <ScopeStrip r={result} />
                  </ReportSection>
                  <ReportSection n={2} title="Exposure" sub="A service is exposed only if the range on the last edge admits the malicious version." delay={0.06}>
                    <ExposurePanel r={result} depth={depth} onDepth={onDepth} onSelect={() => {}} />
                    <InsightsPanel r={result} />
                  </ReportSection>
                  <ReportSection n={3} id="who-to-wake" title="Who to wake" sub="Owning team and on-call for every exposed service." delay={0.1}>
                    <WakeCard wake={result.wake} />
                  </ReportSection>
                  <ReportSection n={4} title="Fix" sub="Overrides pinned to clean versions, drafts for each team. Approve it to remember it as a playbook." delay={0.14}>
                    {(result.exposed.length > 0 || result.fix) ? (
                      <FixPanel canFix={result.exposed.length > 0} fix={result.fix} loading={fixLoading} onFix={onFix} onApproved={() => { refreshPlaybooks(); refreshTimeline(); }} />
                    ) : <p className="text-[14px] text-ink-2">Nothing to fix: no service can install a malicious version at this depth.</p>}
                    <PlaybooksPanel playbooks={playbooks} error={pbErr} />
                  </ReportSection>
                  <ReportSection n={5} title="Trace" sub="Every tool call, its Cypher and timing. Nothing is answered from anywhere else." delay={0.18}>
                    <TracePanel trace={result.trace} loading={false} />
                    <MemoryPanel incidents={incidents} onRecall={(q) => run(q)} onReset={canReset ? onReset : undefined} />
                  </ReportSection>
                </div>
              ) : (
                <div className="mt-8">
                  <ReportSection n={1} title="Answer" sub={result.outcome === "unknown_entity" ? "No traversal ran."
                    : isNotHit(result) ? "Every version checked against every service. Nothing to fix."
                    : result.wake.length ? "Ownership, channels and on-call, read from the graph." : "From the graph, with every hop shown."}>
                    {result.outcome === "answered_from_memory" && <MemoryDetail r={result} />}
                    <NotHitView r={result} fleet={fleet} />
                    <ResponderView wake={result.wake} />
                    <SpofPanel r={result} />
                    <PathPanel r={result} />
                    <WakeOnly r={result} />
                    {result.outcome === "unknown_entity" && (
                      <Card className="p-5">
                        <p className="text-[14px] text-ink-2">Nothing was invented. Try one of these:</p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {PRESETS.slice(0, 4).map((p) => (
                            <button key={p.q} onClick={() => run(p.q)} className="press rounded-md px-2 py-1 font-mono text-[12.5px] text-ink ring-1 ring-inset ring-line hover:bg-subtle">{p.q}</button>
                          ))}
                        </div>
                      </Card>
                    )}
                  </ReportSection>
                  <ReportSection n={2} title="Trace" sub="Every tool call, its Cypher and timing.">
                    <TracePanel trace={result.trace} loading={false} />
                    <MemoryPanel incidents={incidents} onRecall={(q) => run(q)} onReset={canReset ? onReset : undefined} />
                  </ReportSection>
                </div>
              )}
            </div>
          )}
        </div>

        {up && (
          <PageSection id="watch" eyebrow="Live watch" title="New npm malware, checked as it lands"
            sub="PatientZero polls the OSV npm index for new malicious-package advisories, writes each one into every tenant's graph and traces it through the dependency chains at depth 6, with npm semver on the last edge.">
            <LiveWatch tenant={tenant} tenants={tenants} />
          </PageSection>
        )}

        {up && (
          <PageSection id="timeline" eyebrow="Timeline" title="From compromise to remediation"
            sub="Every dated event in the graph: the compromise, the OSV advisories, incidents written back by the agent, and playbooks a human approved.">
            <Card className="scroll-thin max-h-[560px] overflow-y-auto p-6">{timeline ? <Timeline events={timeline} /> : <Skeleton className="h-40" />}</Card>
          </PageSection>
        )}

        {up && (
          <PageSection id="performance" eyebrow="Performance" title="Under concurrent load"
            sub="Many clients at once running the same exposure, who-to-wake and write queries the app runs, against FalkorDB. The numbers are from the last saved benchmark run.">
            <BenchPanel />
          </PageSection>
        )}

        {up && <ByoSection />}

        <footer className="mt-24 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6 text-[12.5px] text-ink-3">
          <span>The {tenants.find((x) => x.id === tenant)?.company ?? "Acme"} fleet, its teams and people are synthetic. npm packages, versions, ranges (deps.dev) and advisories (OSV) are real.</span>
          <span>Every answer is a Cypher query or algo call against FalkorDB.</span>
        </footer>
      </main>
    </div>
  );
}

function PageSection({ id, eyebrow, title, sub, children }: { id?: string; eyebrow: string; title: string; sub: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 pt-24">
      <p className="eyebrow">{eyebrow}</p>
      <h2 className="mt-2 text-[28px] font-semibold tracking-[-0.02em] text-ink">{title}</h2>
      <p className="mt-2 max-w-[640px] text-[15px] leading-relaxed text-ink-2">{sub}</p>
      <div className="mt-8">{children}</div>
    </section>
  );
}

function Legend({ kind, label }: { kind: "service" | "version" | "exposed" | "safe"; label: string }) {
  const dot = {
    service: "h-2 w-2 rounded-full bg-ink",
    version: "h-1.5 w-1.5 rounded-[2px] bg-ink-3",
    exposed: "h-2 w-2 rounded-full bg-signal",
    safe: "h-2 w-2 rounded-full border border-ink-2 bg-surface",
  }[kind];
  return <span className="flex items-center gap-1.5"><span className={dot} />{label}</span>;
}

function ReportSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading results">
      <div className="flex items-end justify-between gap-6 pb-8">
        <div className="space-y-3"><Skeleton className="h-4 w-48" /><Skeleton className="h-10 w-[420px] max-w-[70vw]" /><Skeleton className="h-4 w-[560px] max-w-[80vw]" /></div>
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="grid gap-10 border-t border-line py-10 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="space-y-2"><Skeleton className="h-3 w-6" /><Skeleton className="h-5 w-28" /><Skeleton className="h-3 w-40" /></div>
          <div className="space-y-3">{[0, 1, 2].map((j) => <Skeleton key={j} className="h-14" />)}</div>
        </div>
      ))}
    </div>
  );
}

function EmptyState({ onPick, tenant }: { onPick: (q: string) => void; tenant: string }) {
  const items = tenant === "globex" ? [
    { n: "01", title: "Replay Shai-Hulud on Globex", body: "The September 2025 worm against Globex's 12 services: what is exposed, through which chain, and who owns it.", q: "Shai-Hulud" },
    { n: "02", title: "Replay the qix compromise", body: "The same 19 malicious versions Acme faced, traced through Globex's own fleet.", q: "maintainer qix was phished" },
    { n: "03", title: "Find your single point of failure", body: "Rank npm accounts by how many Globex services they could reach if phished.", q: "who is our single point of failure?" },
  ] : [
    { n: "01", title: "Replay the qix compromise", body: "Resolve the maintainer, scope the 19 malicious versions, walk every chain and find the owners.", q: "maintainer qix was phished" },
    { n: "02", title: "Find your single point of failure", body: "Rank npm accounts by how many services they could reach if phished.", q: "who is our single point of failure?" },
    { n: "03", title: "Explain a hidden dependency", body: "The shortest path from a service to a package, every range on the way.", q: "why is img-service connected to is-arrayish?" },
  ];
  return (
    <div className="grid gap-px overflow-hidden rounded-xl border border-line bg-line md:grid-cols-3">
      {items.map((it, i) => (
        <motion.button key={it.title} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, delay: 0.1 + i * 0.05 }}
          onClick={() => onPick(it.q)} className="group bg-surface p-6 text-left transition-colors hover:bg-subtle">
          <p className="font-mono text-[12px] text-ink-3">{it.n}</p>
          <h3 className="mt-3 text-[16px] font-semibold tracking-[-0.01em] text-ink">{it.title}</h3>
          <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{it.body}</p>
          <span className="mt-5 inline-flex items-center gap-1.5 font-mono text-[12.5px] text-ink-2 group-hover:text-ink">
            {it.q} <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
          </span>
        </motion.button>
      ))}
    </div>
  );
}

/** The user's words stay in the bar; when the graph read them as something more specific, say so underneath. */
function Interpreted({ result, asked }: { result: AskResponse | null; asked: string }) {
  if (!result) return null;
  const canon = result.outcome === "answered_from_memory"
    ? result.memory?.seen_before?.trigger ?? result.memory?.incidents?.[0]?.trigger ?? result.trigger
    : result.trigger;
  const norm = (t: string) => t.toLowerCase().replace(/\s+/g, " ").trim();
  if (!canon || !asked || norm(asked).includes(norm(canon))) return null;
  return (
    <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }} data-testid="interpreted"
      className="mt-2 pl-1 text-[12.5px] text-ink-3">
      Interpreted as: <span className="font-mono text-ink-2">{canon}</span>
    </motion.p>
  );
}

/** Real numbers from the last answer's trace: wall time, Cypher queries run and rows read. Nothing estimated. */
function LastTraversal({ result, loading, queries }: { result: AskResponse | null; loading: boolean; queries?: number }) {
  const steps = result?.trace.filter((t) => t.cypher) ?? [];
  const rows = steps.reduce((a, t) => a + (t.rows || 0), 0);
  let body: React.ReactNode;
  if (loading) body = <><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-3" />Traversing the graph</>;
  else if (result) body = (
    <>
      <span className="h-1.5 w-1.5 rounded-full bg-safe" />
      Last traversal <span className="font-mono text-ink-2">{result.total_ms.toFixed(0)} ms</span>
      <span className="text-line-strong">/</span>
      <span className="tabular">{steps.length} Cypher quer{steps.length === 1 ? "y" : "ies"}</span>
      <span className="text-line-strong">/</span>
      <span className="tabular">{rows.toLocaleString()} rows read</span>
    </>
  );
  else if (queries) body = <><span className="h-1.5 w-1.5 rounded-full bg-line-strong" />{queries} parameterised Cypher queries, none generated from your text</>;
  else return <div className="h-5" />;
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.p key={loading ? "l" : result ? result.query + result.total_ms : "idle"} initial={{ opacity: 0, y: 2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
        className="flex h-5 items-center gap-2 text-[12.5px] text-ink-3">
        {body}
      </motion.p>
    </AnimatePresence>
  );
}
