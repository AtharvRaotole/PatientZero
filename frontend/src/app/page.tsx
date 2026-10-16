"use client";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { ArrowRight, Boxes, Command, CornerDownLeft, FlaskConical, GitBranch, Loader2, Radar, Search, ShieldAlert, Sparkles, Waypoints } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiGet, apiPost } from "@/lib/api";
import type { AskResponse, FleetGraph, Health, Incident } from "@/lib/types";
import {
  ErrorCard, ExposurePanel, FixPanel, InsightsPanel, MemoryDetail, MemoryPanel, OutcomeBanner, PathPanel, ScopeStrip, SpofPanel,
  TracePanel, WakeCard, WakeOnly,
} from "@/components/panels";
import { Card, Pill, Skeleton } from "@/components/ui";
import { AgentAnswer, PlaybooksPanel } from "@/components/agentui";
import ByoSection from "@/components/ByoSection";
import { Timeline, type TimelineEvent } from "@/components/viz";
import { CalendarRange } from "lucide-react";
import { CardHeader } from "@/components/ui";
import type { Playbook } from "@/lib/types";

const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });

const PRESETS: { q: string; hint: string }[] = [
  { q: "maintainer qix was phished", hint: "Replay Sept 8 2025" },
  { q: "debug@4.4.2", hint: "Honest negatives" },
  { q: "GHSA-4x49-vf9v-38px", hint: "Advisory id" },
  { q: "who is our single point of failure?", hint: "algo.pageRank" },
  { q: "why is img-service connected to is-arrayish?", hint: "algo.SPpaths" },
  { q: "who do I wake for checkout-api", hint: "On-call" },
  { q: "ansi-regex@6.2.1", hint: "not_in_graph" },
  { q: "left-pad@1.3.0", hint: "unknown_entity" },
];

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
  const [depth, setDepth] = useState(4);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fixLoading, setFixLoading] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const [mode, setMode] = useState<"auto" | "deterministic">("auto");
  const [playbooks, setPlaybooks] = useState<Playbook[] | null>(null);
  const [pbErr, setPbErr] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);

  const refreshTimeline = useCallback(async () => {
    try {
      setTimeline((await apiGet<{ events: TimelineEvent[] }>("/api/timeline")).events);
    } catch {
      setTimeline([]);
    }
  }, []);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const loadMeta = useCallback(async () => {
    try {
      const h = await apiGet<Health>("/health");
      setHealth(h);
      setHealthErr(null);
      if (h.falkordb === "up" && h.graph_loaded) {
        const [g, inc] = await Promise.all([
          apiGet<FleetGraph>("/api/fleet/graph"),
          apiGet<{ incidents: Incident[] }>("/api/incidents"),
        ]);
        setFleet(g);
        setIncidents(inc.incidents);
      }
    } catch (e) {
      setHealth(null);
      setHealthErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

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
  }, [refreshPlaybooks, refreshTimeline]);

  const refreshIncidents = useCallback(async () => {
    try {
      const inc = await apiGet<{ incidents: Incident[] }>("/api/incidents");
      setIncidents(inc.incidents);
    } catch {
      /* memory list is secondary; ignore */
    }
  }, []);

  const run = useCallback(
    async (q: string, opts: { depth?: number; fresh?: boolean } = {}) => {
      const text = q.trim();
      if (!text) return;
      setQuery(text);
      setLastQuery(text);
      setLoading(true);
      setErr(null);
      try {
        const r = await apiPost<AskResponse>("/api/ask", { query: text, depth: opts.depth ?? depth, fresh: !!opts.fresh, session: sessionId(), mode });
        setResult(r);
        setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 450);
      } catch (e) {
        const a = e as ApiError;
        setErr({ message: a.message, kind: a.kind ?? "error" });
        setResult(null);
        if (a.kind === "graph_unavailable" || a.kind === "network") loadMeta();
      } finally {
        setLoading(false);
      }
    },
    [depth, loadMeta, mode],
  );

  const onDepth = (d: number) => {
    setDepth(d);
    if (lastQuery) run(lastQuery, { depth: d, fresh: true });
  };

  const onFix = async () => {
    if (!lastQuery) return;
    setFixLoading(true);
    try {
      const r = await apiPost<AskResponse>("/api/fix", { query: lastQuery, depth, session: sessionId() });
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

  return (
    <div className="relative min-h-screen overflow-x-hidden">
      <div className="aurora" />
      <div className="grid-bg pointer-events-none fixed inset-0 z-0" />

      {/* Top bar */}
      <header className="relative z-20 mx-auto flex max-w-[1500px] items-center justify-between px-6 pt-5">
        <div className="flex items-center gap-3">
          <div className="relative grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-rose-500 via-fuchsia-500 to-indigo-500 shadow-[0_0_30px_-4px_rgba(255,77,109,0.7)]">
            <Radar className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-[15px] font-semibold tracking-tight">PatientZero</p>
            <p className="text-[11px] text-white/45">npm supply-chain incident agent on FalkorDB</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Pill tone="warn"><FlaskConical className="h-3 w-3" />Synthetic Acme fleet, real npm data</Pill>
          {health ? (
            <Pill tone={up ? "safe" : "danger"}>
              <span className={clsx("h-1.5 w-1.5 rounded-full", up ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-rose-400")} />
              FalkorDB {health.falkordb} · {health.graph}
            </Pill>
          ) : healthErr ? (
            <Pill tone="danger">API offline</Pill>
          ) : (
            <Skeleton className="h-5 w-40 rounded-full" />
          )}
          <a href="#byo" className="hidden rounded-full border border-white/10 px-3 py-0.5 text-[11px] text-white/70 transition hover:border-white/25 hover:text-white md:inline-flex">Scan your repo</a>
          <ModeSwitch llm={health?.agent_mode === "llm"} model={health?.llm_model ?? null} mode={mode} setMode={setMode} />
        </div>
      </header>

      {/* Hero: the graph */}
      <main className="relative z-10 mx-auto max-w-[1500px] px-6 pb-24 pt-5">
        <div className="relative h-[680px] overflow-hidden rounded-3xl border border-white/[0.08] bg-[radial-gradient(ellipse_at_center,rgba(20,24,40,0.85),rgba(5,6,10,0.95))] shadow-[0_40px_120px_-40px_rgba(124,156,255,0.35)]">
          {fleet ? (
            <GraphCanvas fleet={fleet} highlight={result?.graph ?? null} onNodeClick={() => {}} />
          ) : (
            <div className="absolute inset-0 grid place-items-center">
              {healthErr || (health && !up) ? null : (
                <div className="flex items-center gap-3 text-sm text-white/45"><Loader2 className="h-4 w-4 animate-spin" /> Loading the fleet graph from FalkorDB</div>
              )}
            </div>
          )}

          {/* overlay: headline + command bar */}
          <div className="pointer-events-none absolute inset-x-0 top-0 p-6 md:p-8">
            <div className="pointer-events-auto max-w-2xl">
              <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7 }}>
                <p className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/30 px-3 py-1 text-[11px] text-white/60 backdrop-blur">
                  <ShieldAlert className="h-3.5 w-3.5 text-rose-300" /> Sept 8 2025: one phished maintainer, 19 packages, ~2B weekly downloads
                </p>
                <h1 className="text-gradient text-4xl font-semibold leading-[1.05] tracking-tight md:text-[52px]">
                  Know your blast radius
                  <br />
                  before the postmortem.
                </h1>
                <p className="mt-3 max-w-xl text-[15px] text-white/55">
                  PatientZero traverses every dependency chain in your fleet, decides exposure with npm semver on the last edge, and tells you who to wake and what to pin.
                </p>
              </motion.div>

              <motion.form
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.7, delay: 0.15 }}
                onSubmit={(e) => { e.preventDefault(); run(query, { fresh: false }); }}
                className="glass-strong mt-6 flex items-center gap-3 rounded-2xl px-4 py-3 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.8)] focus-within:ring-1 focus-within:ring-indigo-400/40"
              >
                <Search className="h-5 w-5 text-white/40" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="maintainer qix was phished, debug@4.4.2, GHSA-..., who do I wake?"
                  className="flex-1 bg-transparent text-[15px] text-white placeholder:text-white/30 focus:outline-none"
                  aria-label="Ask PatientZero"
                />
                <span className="hidden items-center gap-1 rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] text-white/35 sm:flex"><Command className="h-3 w-3" />K</span>
                <button type="submit" disabled={loading || !query.trim() || !up}
                  className="inline-flex items-center gap-2 rounded-xl bg-white px-3.5 py-2 text-sm font-semibold text-black transition hover:bg-white/90 disabled:opacity-40">
                  {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <CornerDownLeft className="h-4 w-4" />} Trace
                </button>
              </motion.form>

              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }} className="mt-3 flex flex-wrap gap-1.5">
                {PRESETS.map((p) => (
                  <button key={p.q} onClick={() => run(p.q, { fresh: p.q.includes("phished") })} disabled={!up || loading}
                    className="group inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-black/35 px-3 py-1 text-[12px] text-white/70 backdrop-blur transition hover:border-white/25 hover:text-white disabled:opacity-40">
                    {p.q}
                    <span className="text-[10px] text-white/30 group-hover:text-white/50">{p.hint}</span>
                  </button>
                ))}
              </motion.div>
            </div>
          </div>

          {/* legend + stats */}
          <div className="pointer-events-none absolute bottom-5 left-6 flex flex-wrap items-center gap-3 text-[11px] text-white/55">
            <Legend color="#7c9cff" label="Service" />
            <Legend color="#94a3b8" label="Package version" />
            <Legend color="#ff4d6d" label="Exposed" glow />
            <Legend color="#fbbf24" label="Exposure chain" />
            <Legend color="#34d399" label="Safe by range" />
            <Legend color="#a78bfa" label="Shortest path" />
          </div>
          <div className="absolute bottom-5 right-6 flex items-center gap-2">
            {fleet && (
              <div className="glass-strong flex items-center gap-4 rounded-xl px-4 py-2 text-[11px] text-white/60">
                <span className="flex items-center gap-1.5"><Boxes className="h-3.5 w-3.5" /><b className="font-semibold text-white/90">{svcCount}</b> services</span>
                <span className="flex items-center gap-1.5"><Waypoints className="h-3.5 w-3.5" /><b className="font-semibold text-white/90">{pkgCount.toLocaleString()}</b> versions</span>
                <span className="flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" /><b className="font-semibold text-white/90">{fleet.edges.length.toLocaleString()}</b> edges</span>
              </div>
            )}
            {isIncident && (
              <div className="glass-strong flex items-center gap-3 rounded-xl px-4 py-2">
                <span className="text-[11px] text-white/50">depth</span>
                <input aria-label="graph depth" type="range" min={1} max={6} value={depth} onChange={(e) => onDepth(Number(e.target.value))} className="depth w-28" />
                <span className="w-3 font-mono text-sm text-white/85">{depth}</span>
              </div>
            )}
          </div>

          <AnimatePresence>
            {loading && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="pointer-events-none absolute inset-0 grid place-items-center bg-black/20">
                <div className="glass-strong flex items-center gap-3 rounded-2xl px-5 py-3 text-sm text-white/80">
                  <Loader2 className="h-4 w-4 animate-spin text-indigo-300" /> Traversing the graph
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Results */}
        <div ref={resultsRef} className="scroll-mt-6 pt-6">
          {healthErr && <ErrorCard kind="network" message={`${healthErr}. Start the backend: cd backend && uv run uvicorn app.main:app --port 8000`} onRetry={loadMeta} />}
          {health && !up && !healthErr && (
            <ErrorCard kind="graph_unavailable" message={health.falkordb === "down" ? "graph unavailable: FalkorDB is not reachable. Run scripts/start-falkordb.sh. PatientZero never answers from anywhere else." : `Graph ${health.graph} is empty. Load it with ingest/load.py.`} onRetry={loadMeta} />
          )}
          {err && <div className="mb-4"><ErrorCard kind={err.kind} message={err.message} onRetry={() => (lastQuery ? run(lastQuery) : loadMeta())} /></div>}

          {!result && !loading && !err && up && <EmptyState onPick={(q) => run(q, { fresh: q.includes("phished") })} />}

          {loading && !result && (
            <div className="grid gap-4 lg:grid-cols-3">
              <Skeleton className="h-28 lg:col-span-3" />
              <Skeleton className="h-96 lg:col-span-2" />
              <Skeleton className="h-96" />
            </div>
          )}

          {result && (
            <div className={clsx("space-y-4 transition-opacity", loading && "opacity-50")}>
              <OutcomeBanner r={result} onFresh={() => run(lastQuery, { fresh: true })} />
              <AgentAnswer key={result.query + result.total_ms} r={result} llmAvailable={health?.agent_mode === "llm"} />
              <ScopeStrip r={result} />
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
                <div className="space-y-4">
                  {isIncident && <ExposurePanel r={result} depth={depth} onDepth={onDepth} onSelect={() => {}} />}
                  {isIncident && <InsightsPanel r={result} />}
                  {result.outcome === "answered_from_memory" && <MemoryDetail r={result} />}
                  <SpofPanel r={result} />
                  <PathPanel r={result} />
                  <WakeOnly r={result} />
                  {result.outcome === "unknown_entity" && (
                    <Card className="p-6">
                      <p className="text-sm text-white/70">No traversal ran. Nothing was invented. Try one of these:</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {PRESETS.slice(0, 4).map((p) => (
                          <button key={p.q} onClick={() => run(p.q)} className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/70 hover:border-white/25">{p.q}</button>
                        ))}
                      </div>
                    </Card>
                  )}
                  {(result.exposed.length > 0 || result.fix) && <FixPanel canFix={result.exposed.length > 0} fix={result.fix} loading={fixLoading} onFix={onFix} onApproved={() => { refreshPlaybooks(); refreshTimeline(); }} />}
                </div>
                <div className="space-y-4">
                  <TracePanel trace={result.trace} loading={false} />
                  <WakeCard wake={result.wake} />
                  <MemoryPanel incidents={incidents} onRecall={(q) => run(q)} onReset={onReset} />
                  <PlaybooksPanel playbooks={playbooks} error={pbErr} />
                </div>
              </div>
            </div>
          )}
        </div>

        {up && (
          <section className="mt-14">
            <Card>
              <CardHeader icon={<CalendarRange className="h-4 w-4" />} title="From compromise to remediation" sub="Every dated event in the graph: the curated compromise, OSV advisories, incidents and approved playbooks" />
              <div className="scroll-thin max-h-[520px] overflow-y-auto p-6">{timeline ? <Timeline events={timeline} /> : <Skeleton className="h-40" />}</div>
            </Card>
          </section>
        )}

        {up && <ByoSection />}

        <footer className="mt-16 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-6 text-[11px] text-white/35">
          <span>Acme, its services, teams and people are synthetic. npm packages, versions, dependency ranges (deps.dev) and advisories (OSV) are real.</span>
          <span>Every answer comes from a Cypher query or algo.* call against FalkorDB. No LLM required.</span>
        </footer>
      </main>
    </div>
  );
}

function ModeSwitch({ llm, model, mode, setMode }: { llm: boolean; model: string | null; mode: "auto" | "deterministic"; setMode: (m: "auto" | "deterministic") => void }) {
  if (!llm) return <Pill tone="accent">Deterministic router</Pill>;
  return (
    <div className="flex items-center rounded-full bg-white/[0.05] p-0.5 text-[11px] ring-1 ring-inset ring-white/10" title="Auto: deterministic router first, the LLM only picks tools when the router is unsure">
      {(["auto", "deterministic"] as const).map((m) => (
        <button key={m} onClick={() => setMode(m)} className={clsx("rounded-full px-2.5 py-0.5 font-medium transition", mode === m ? "bg-white/15 text-white" : "text-white/50 hover:text-white/80")}>
          {m === "auto" ? `LLM assist · ${model ?? "gpt-4o-mini"}` : "Deterministic"}
        </button>
      ))}
    </div>
  );
}

function Legend({ color, label, glow }: { color: string; label: string; glow?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-black/30 px-2 py-0.5 backdrop-blur">
      <span className="h-2 w-2 rounded-full" style={{ background: color, boxShadow: glow ? `0 0 10px ${color}` : undefined }} />
      {label}
    </span>
  );
}

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  const items = [
    { icon: <ShieldAlert className="h-5 w-5 text-rose-300" />, title: "Replay the qix compromise", body: "Resolve the maintainer, scope the 19 malicious versions, traverse every chain, and page the owners.", q: "maintainer qix was phished" },
    { icon: <Sparkles className="h-5 w-5 text-violet-300" />, title: "Find your single point of failure", body: "Rank npm accounts by how many Acme services they could reach if phished, with algo.pageRank.", q: "who is our single point of failure?" },
    { icon: <GitBranch className="h-5 w-5 text-indigo-300" />, title: "Explain a hidden dependency", body: "Shortest path from a service to a package with algo.SPpaths, every hop's range shown.", q: "why is img-service connected to is-arrayish?" },
  ];
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {items.map((it, i) => (
        <Card key={it.title} delay={0.1 + i * 0.08} className="group cursor-pointer p-5 transition hover:border-white/20">
          <button onClick={() => onPick(it.q)} className="text-left">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-white/[0.06]">{it.icon}</span>
            <h3 className="mt-4 text-[15px] font-semibold tracking-tight text-white/90">{it.title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-white/50">{it.body}</p>
            <span className="mt-4 inline-flex items-center gap-1.5 font-mono text-[12px] text-white/60 group-hover:text-white">{it.q} <ArrowRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" /></span>
          </button>
        </Card>
      ))}
    </div>
  );
}
