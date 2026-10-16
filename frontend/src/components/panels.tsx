"use client";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { Activity, AlertTriangle, BellRing, ChevronDown, Crown, GitBranch, History, Loader2, RotateCcw, Terminal, Wand2 } from "lucide-react";
import { useState } from "react";
import type { AskResponse, Fix, Incident, ServiceExposure, TraceStep, WakeEntry } from "@/lib/types";
import { Button, Card, CardHeader, CopyButton, CountUp, PathChain, Segmented, ShowMore, Skeleton } from "./ui";
import { ApproveBar } from "./agentui";
import { BlastRings, ExposureMatrix, SpofScatter } from "./viz";
import type { Playbook } from "@/lib/types";

const HEADLINE: Record<string, string> = {
  answered_from_memory: "Answered from graph memory.",
  not_in_graph: "No exposure found.",
  unknown_entity: "Not in the graph.",
};

export function OutcomeBanner({ r, onFresh }: { r: AskResponse; onFresh: () => void }) {
  const tokens = r.trace.reduce((a, s) => a + (Number((s as { tokens?: number }).tokens) || 0), 0);
  const hit = r.outcome === "answered_with_path" && r.exposed.length > 0;
  const meta = [
    r.trigger ? `Trigger: ${r.trigger}` : null,
    r.exposed.length || r.safe.length ? `depth ${r.depth}` : null,
    `${r.total_ms.toFixed(0)} ms`,
    r.mode === "llm" ? `LLM${r.llm_model ? ` ${r.llm_model}` : ""}, ${tokens} tokens` : "Deterministic",
  ].filter(Boolean) as string[];
  return (
    <motion.header initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <p className="flex flex-wrap items-center gap-x-2 text-[13px] text-ink-3">
        <span className="font-medium text-ink-2">{r.outcome === "answered_from_memory" ? "Graph memory" : r.exposed.length || r.safe.length ? "Incident report" : "Answer"}</span>
        {meta.map((m) => <span key={m} className="flex items-center gap-2"><span className="text-line-strong">/</span>{m}</span>)}
      </p>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-6">
        <div className="min-w-0 max-w-[820px]">
          {hit ? (
            <h2 className="text-[28px] font-semibold leading-tight tracking-[-0.02em] text-ink">
              <CountUp key={r.query + r.depth} value={r.exposed.length} className="text-signal" /> of {r.fleet_size} services can install a malicious version.
            </h2>
          ) : (
            <h2 className="text-[24px] font-semibold leading-tight tracking-[-0.02em] text-ink md:text-[28px]">{HEADLINE[r.outcome] ?? split(r.message)[0]}</h2>
          )}
          {r.outcome === "answered_from_memory" && <MemoryLine r={r} />}
          {detail(r, hit) && <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{detail(r, hit)}</p>}
        </div>
        {r.outcome === "answered_from_memory" && (
          <button onClick={onFresh} className="press inline-flex h-9 items-center gap-2 rounded-lg bg-ink px-3.5 text-[13px] font-medium text-paper hover:bg-ink/85">
            <RotateCcw className="h-4 w-4" /> Run a fresh traversal
          </button>
        )}
      </div>
      {(r.exposed.length > 0 || r.safe.length > 0) && r.fleet_size ? <FleetStrip r={r} /> : null}
    </motion.header>
  );
}

/** Which stored incident answered, and when: the demo points at this line. */
function MemoryLine({ r }: { r: AskResponse }) {
  const sb = r.memory?.seen_before;
  const inc = r.memory?.incidents?.[0];
  const id = sb?.id ?? inc?.id;
  if (!id) return null;
  const when = sb?.at_local ?? (inc?.at ? new Date(inc.at).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : "");
  return (
    <p data-testid="memory-line" className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] text-ink-2">
      <History className="h-4 w-4 text-ink-3" />
      Incident <span className="font-mono text-[13px] text-ink">{id}</span>
      {when && <><span className="text-line-strong">/</span><span className="tabular">{when}</span></>}
    </p>
  );
}

/** First sentence (or the clause before a colon) as the headline, the rest as detail. Version dots never split. */
function split(msg: string): [string, string] {
  const m = msg.match(/^(.+?)(?::\s+|\.\s+(?=[A-Z])|\.$)([\s\S]*)$/);
  if (!m) return [msg, ""];
  return [m[1].trim() + ".", m[2].trim()];
}

/** The backend message minus what the headline and the Seen-before row already say. */
function detail(r: AskResponse, hit: boolean): string {
  let m = r.message;
  if (r.outcome === "answered_from_memory") {
    const sb = r.memory?.seen_before, inc = r.memory?.incidents?.[0];
    const n = sb?.exposed_count ?? inc?.exposed_count, d = sb?.depth ?? inc?.depth, t = sb?.trigger ?? inc?.trigger;
    if (n !== undefined) return `${n} service${n === 1 ? " was" : "s were"} exposed at depth ${d}${t ? ` for "${t}"` : ""}.${sb?.has_patch || inc?.patch ? " The patch is on file." : ""} Run a fresh traversal to re-check against today's graph.`;
  }
  if (hit) m = m.replace(/^\s*\d+ of \d+ services can install a malicious version[^.]*\.\s*/, "");
  else if (!HEADLINE[r.outcome]) m = split(m)[1];
  if (r.memory?.seen_before && r.outcome !== "answered_from_memory") m = m.replace(/\s*Seen before:[\s\S]*$/, "");
  return m.trim();
}

/** One cell per fleet service: exposed, reached but excluded by range, or never reached. */
function FleetStrip({ r }: { r: AskResponse }) {
  const cells = [
    ...r.exposed.map((e) => ({ s: e.service, k: "exposed" as const })),
    ...r.safe.map((e) => ({ s: e.service, k: "safe" as const })),
    ...r.clean.map((s) => ({ s, k: "clean" as const })),
  ];
  return (
    <div className="mt-6">
      <div className="flex gap-1" role="img" aria-label={`${r.exposed.length} exposed, ${r.safe.length} excluded by range, ${r.clean.length} not reached`}>
        {cells.map((c, i) => (
          <motion.span key={c.s} title={`${c.s}: ${c.k === "exposed" ? "exposed" : c.k === "safe" ? "reached, range excludes it" : "never reaches an affected package"}`}
            initial={{ opacity: 0, scaleY: 0.4 }} animate={{ opacity: 1, scaleY: 1 }} transition={{ duration: 0.2, delay: 0.15 + i * 0.015 }}
            className={clsx("h-2.5 flex-1 rounded-[3px]", c.k === "exposed" ? "bg-signal" : c.k === "safe" ? "border border-ink-3 bg-surface" : "bg-line")} />
        ))}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-ink-2">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] bg-signal" /><b className="font-medium text-ink tabular">{r.exposed.length}</b> exposed</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] border border-ink-3" /><b className="font-medium text-ink tabular">{r.safe.length}</b> reached, excluded by range</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-[2px] bg-line" /><b className="font-medium text-ink tabular">{r.clean.length}</b> never reached</span>
      </div>
    </div>
  );
}

function ServiceRow({ e, exposed, open, onToggle }: { e: ServiceExposure; exposed: boolean; open: boolean; onToggle: () => void }) {
  const main = e.findings.filter((f) => f.exposed === exposed || !exposed);
  return (
    <div className="group">
      <button onClick={onToggle} aria-expanded={open} className="flex w-full items-start gap-3 px-5 py-3.5 text-left transition-colors hover:bg-subtle/70">
        <span className={clsx("mt-[7px] h-2 w-2 shrink-0 rounded-full", exposed ? "bg-signal" : "border border-ink-3")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex min-w-0 items-baseline gap-2.5">
              <span className="truncate text-[14px] font-medium text-ink">{e.service}</span>
              <span className="hidden text-[12.5px] text-ink-3 sm:inline">Tier {e.tier} · {e.team}</span>
            </div>
            <div className="flex shrink-0 items-center gap-3 text-[12.5px]">
              <span className={clsx("font-mono", exposed ? "text-signal-ink" : "text-ink-2")}>{exposed ? e.exposed_packages.join(", ") : "excluded by range"}</span>
              <span className="w-12 text-right text-ink-3 tabular">{main[0].path.hops} hop{main[0].path.hops > 1 ? "s" : ""}</span>
              <ChevronDown className={clsx("h-4 w-4 text-ink-3 transition-transform duration-200", open && "rotate-180")} />
            </div>
          </div>
          <div className="mt-1.5">
            <PathChain via={main[0].path.via} requirement={main[0].path.requirement} bad={main[0].bad_version} exposed={main[0].path.exposed} compact omitRoot />
            {!exposed && main[0].path.reason && <p className="mt-1 text-[12.5px] text-ink-3">{main[0].path.reason}</p>}
          </div>
        </div>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
            <div className="space-y-4 bg-subtle/60 py-4 pl-10 pr-5">
              {e.findings.map((f, i) => (
                <div key={i} className="space-y-1.5">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px]">
                    <span className="font-mono font-medium text-ink">{f.package}@{f.bad_version}</span>
                    {f.advisory_id && <span className="font-mono text-ink-3">{f.advisory_id}{f.ghsa ? ` · ${f.ghsa}` : ""}</span>}
                    <span className="text-ink-3">{f.path_count} path{f.path_count > 1 ? "s" : ""} in the graph</span>
                  </div>
                  <PathChain via={f.path.via} requirement={f.path.requirement} bad={f.bad_version} exposed={f.path.exposed} />
                  <p className={clsx("text-[12.5px]", f.exposed ? "text-signal-ink" : "text-ink-2")}>{f.path.reason}</p>
                  {f.alternatives.slice(0, 2).map((a, j) => (
                    <div key={j} className="opacity-60"><PathChain via={a.via} requirement={a.requirement} bad={f.bad_version} exposed={a.exposed} compact /></div>
                  ))}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ExposurePanel({ r, depth, onSelect }: { r: AskResponse; depth: number; onDepth: (d: number) => void; onSelect: (svc: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [tab, setTab] = useState<"exposed" | "safe">("exposed");
  const list = tab === "exposed" ? r.exposed : r.safe;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <Segmented value={tab} onChange={setTab} options={[
          { value: "exposed", label: <>Exposed <span className="ml-1 text-ink-3 tabular">{r.exposed.length}</span></> },
          { value: "safe", label: <>Excluded by range <span className="ml-1 text-ink-3 tabular">{r.safe.length}</span></> },
        ]} />
        <span className="text-[12.5px] text-ink-3">{r.clean.length} services never reach an affected package</span>
      </div>
      <div className="divide-y divide-line">
        {list.length === 0 && (
          <p className="px-5 py-10 text-center text-[14px] text-ink-3">
            {tab === "exposed" ? `No fleet path admits a malicious version at depth ${depth}.` : "No service reaches an affected package with a range that excludes it."}
          </p>
        )}
        <ShowMore key={tab} limit={6} noun="services">
          {list.map((e) => (
            <ServiceRow key={e.service} e={e} exposed={tab === "exposed"} open={open === e.service}
              onToggle={() => { setOpen(open === e.service ? null : e.service); onSelect(e.service); }} />
          ))}
        </ShowMore>
      </div>
    </Card>
  );
}

export function TracePanel({ trace, loading }: { trace: TraceStep[]; loading: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  const total = trace.reduce((a, s) => a + (s.ms || 0), 0);
  return (
    <Card>
      <CardHeader icon={<Terminal />} title="Agent trace" sub="A fixed catalogue of tools. Human-written Cypher from queries/*.cypher."
        right={<span className="font-mono text-[12px] text-ink-3 tabular">{trace.length} steps · {total.toFixed(1)} ms</span>} />
      <div className="divide-y divide-line">
        {loading && <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-8" />)}</div>}
        {!loading && trace.length === 0 && <p className="py-6 text-center text-[13px] text-ink-3">Tool calls appear here.</p>}
        {!loading && <ShowMore limit={10} noun="steps">{trace.map((s, i) => !s.cypher ? (
          <motion.div key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.03, duration: 0.2 }}
            className="flex items-center gap-3 px-5 py-2.5 text-[13px]">
            <span className="w-5 font-mono text-[12px] text-ink-3 tabular">{String(i + 1).padStart(2, "0")}</span>
            <span className="font-mono text-ink-2">{s.tool}</span>
            <span className={clsx("truncate", s.error ? "text-signal-ink" : "text-ink-3")}>{s.error ? `${s.note ?? ""} ${s.error}` : s.note}</span>
            {s.ms > 0 && <span className="ml-auto shrink-0 font-mono text-[12px] text-ink-3 tabular">{s.ms.toFixed(0)} ms</span>}
          </motion.div>
        ) : (
          <motion.div key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.03, duration: 0.2 }}>
            <button onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i} className="flex w-full items-center gap-3 px-5 py-2.5 text-left text-[13px] transition-colors hover:bg-subtle/70">
              <span className="w-5 font-mono text-[12px] text-ink-3 tabular">{String(i + 1).padStart(2, "0")}</span>
              <span className="font-mono font-medium text-ink">{s.tool}</span>
              {s.tool === "memory lookup" && s.note ? <span className="truncate text-[12.5px] text-ink-2">{s.note}</span>
                : s.query && s.query !== s.tool && <span className="hidden truncate font-mono text-[12px] text-ink-3 sm:inline">{s.query}</span>}
              {s.write && <span className="text-[12px] font-medium text-risk">write</span>}
              {s.error && <span className="text-[12px] font-medium text-signal-ink">error</span>}
              <span className="ml-auto flex shrink-0 items-center gap-4 font-mono text-[12px] tabular">
                <span className="w-16 text-right text-ink-3">{s.tool === "memory lookup" ? "" : `${s.rows} row${s.rows === 1 ? "" : "s"}`}</span>
                <span className="w-16 text-right text-ink-2">{s.ms.toFixed(1)} ms</span>
              </span>
              <ChevronDown className={clsx("h-3.5 w-3.5 text-ink-3 transition-transform duration-200", open === i && "rotate-180")} />
            </button>
            <AnimatePresence initial={false}>
              {open === i && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }} className="overflow-hidden">
                  <div className="space-y-2 px-5 pb-4 pl-[52px]">
                    {s.note && <p className="text-[12.5px] text-ink-2">{s.note}</p>}
                    {s.error && <p className="text-[12.5px] text-signal-ink">{s.error}</p>}
                    <pre className="scroll-thin overflow-x-auto rounded-lg border border-line bg-subtle p-3 font-mono text-[12px] leading-relaxed text-ink">{s.cypher}</pre>
                    <Params params={s.params} />
                    {s.file && <p className="font-mono text-[11.5px] text-ink-3">{s.file}</p>}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        ))}</ShowMore>}
      </div>
    </Card>
  );
}

/** Query parameters on one line, ellipsized, with an expand toggle when they do not fit. */
function Params({ params }: { params: unknown }) {
  const [open, setOpen] = useState(false);
  const text = JSON.stringify(params ?? {});
  if (text === "{}") return null;
  const long = text.length > 110;
  return (
    <div className="flex items-start gap-2 font-mono text-[12px] text-ink-3">
      <span className="shrink-0">params</span>
      <span className={clsx("min-w-0 flex-1 text-ink-2", open ? "whitespace-pre-wrap break-all" : "truncate")}>{open ? JSON.stringify(params, null, 2) : text}</span>
      {long && <button onClick={() => setOpen(!open)} className="shrink-0 font-sans text-[12px] font-medium text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">{open ? "Collapse" : "Expand"}</button>}
    </div>
  );
}

const initials = (n: string) => n.split(" ").map((p) => p[0]).join("").slice(0, 2);

export function WakeCard({ wake }: { wake: WakeEntry[] }) {
  if (!wake.length) return null;
  return (
    <Card>
      <CardHeader icon={<BellRing />} title={`${wake.length} owning team${wake.length > 1 ? "s" : ""}`} sub="Synthetic Acme org. Page the on-call, then the team channel." />
      <div className="divide-y divide-line">
        <ShowMore limit={6} noun="teams to page">
        {wake.map((w) => (
          <div key={w.service} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_auto] items-center gap-4 px-5 py-3">
            <div className="min-w-0">
              <p className="truncate text-[14px] font-medium text-ink">{w.service}</p>
              <p className="truncate text-[12.5px] text-ink-3">{w.team_display} · <span className="font-mono">{w.slack}</span></p>
            </div>
            <div className="min-w-0 space-y-1">
              {w.oncall.length ? w.oncall.map((o) => (
                <div key={o.handle} className="flex min-w-0 items-center gap-2">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-subtle text-[10.5px] font-semibold text-ink-2 ring-1 ring-line">{initials(o.name)}</span>
                  <span className="truncate text-[13px] text-ink">{o.name} <span className="text-ink-3">{o.handle}</span></span>
                </div>
              )) : <span className="text-[13px] text-ink-3">No on-call</span>}
            </div>
            <span className="text-right text-[12.5px] text-ink-3">Tier {w.tier}<span className="hidden md:inline"> · {w.pager}</span></span>
          </div>
        ))}
        </ShowMore>
      </div>
    </Card>
  );
}

function CodeBlock({ label, text, copyLabel, maxH }: { label: string; text: string; copyLabel?: string; maxH?: string }) {
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="truncate text-[12.5px] font-medium text-ink-2">{label}</span>
        <CopyButton text={text} label={copyLabel} />
      </div>
      <pre className={clsx("scroll-thin overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-subtle p-3.5 font-mono text-[12px] leading-relaxed text-ink", maxH)}>{text}</pre>
    </div>
  );
}

export function FixPanel({ canFix, fix, loading, onFix, onApproved }: { canFix: boolean; fix: Fix | null; loading: boolean; onFix: () => void; onApproved: (p: Playbook) => void }) {
  const [sel, setSel] = useState(0);
  if (!canFix && !fix) return null;
  const s = fix?.per_service[sel];
  return (
    <Card>
      <CardHeader icon={<Wand2 />} title="Remediation" sub="Drafts only. Nothing is sent or merged."
        right={<Button variant={fix ? "secondary" : "primary"} onClick={onFix} disabled={loading}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}{loading ? "Generating" : fix ? "Regenerate" : "Generate fix"}</Button>} />
      {!fix && !loading && <p className="px-5 py-5 text-[14px] leading-relaxed text-ink-2">Builds an overrides patch per service with the clean versions from the advisories, drafts a message to each owning team, and writes the incident back to FalkorDB as memory.</p>}
      {loading && <div className="space-y-2 p-5"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-28" /></div>}
      {fix && s && (
        <div className="space-y-5 p-5">
          <p className="text-[13px] text-ink-2">
            Incident <span className="font-mono text-ink">{fix.incident_id}</span> written to the graph with {fix.exposed_edges} exposure edges.
          </p>
          {(fix.playbooks ?? []).map((pb) => (
            <div key={pb.id} className={clsx("rounded-lg border px-4 py-3 text-[13px] leading-relaxed", pb.agrees_with_proposed_fix ? "border-line bg-subtle text-ink-2" : "border-risk/30 bg-risk-soft text-risk")}>
              <p className="mb-0.5 font-medium text-ink">Reusing an approved playbook</p>
              {pb.message}
            </div>
          ))}
          <ApproveBar key={fix.incident_id} incidentId={fix.incident_id} onApproved={onApproved} />
          <CodeBlock label="Fleet-wide overrides" text={fix.combined_json} />
          <div>
            <p className="mb-2 text-[12.5px] font-medium text-ink-2">Per service</p>
            <div className="scroll-thin flex gap-1 overflow-x-auto pb-1">
              {fix.per_service.map((p, i) => (
                <button key={p.service} onClick={() => setSel(i)}
                  className={clsx("press shrink-0 rounded-md px-2.5 py-1 text-[12.5px] font-medium", i === sel ? "bg-ink text-paper" : "text-ink-2 ring-1 ring-inset ring-line hover:bg-subtle")}>{p.service}</button>
              ))}
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <CodeBlock label={`${s.service}/package.json`} text={s.overrides_json} />
            <CodeBlock label={`Draft to ${s.slack}`} text={s.draft} copyLabel="Copy draft" maxH="max-h-60" />
          </div>
        </div>
      )}
    </Card>
  );
}

export function MemoryPanel({ incidents, onRecall, onReset }: { incidents: Incident[] | null; onRecall: (q: string) => void; onReset?: () => void }) {
  return (
    <Card>
      <CardHeader icon={<History />} title="Incident memory" sub="Incident and Finding nodes read back from the graph."
        right={onReset && incidents && incidents.length > 0 ? <Button variant="ghost" onClick={onReset} className="h-7 text-[12px]">Reset demo memory</Button> : null} />
      <div className="scroll-thin max-h-72 divide-y divide-line overflow-y-auto">
        {incidents === null && <div className="space-y-2 p-4">{[0, 1].map((i) => <Skeleton key={i} className="h-10" />)}</div>}
        {incidents?.length === 0 && <p className="px-5 py-5 text-[13px] text-ink-3">No incidents stored yet. Generate a fix to write one.</p>}
        {incidents?.map((i) => (
          <button key={i.id} onClick={() => onRecall(i.entity_key || i.trigger)} className="flex w-full items-center justify-between gap-4 px-5 py-3 text-left transition-colors hover:bg-subtle/70">
            <div className="min-w-0">
              <p className="truncate font-mono text-[12.5px] text-ink">{i.id}</p>
              <p className="truncate text-[12.5px] text-ink-3">{i.trigger}</p>
            </div>
            <div className="shrink-0 text-right text-[12.5px]">
              <p className="text-ink tabular"><span className="font-semibold text-signal-ink">{i.exposed_count}</span> exposed</p>
              <p className="text-ink-3">{new Date(i.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</p>
            </div>
          </button>
        ))}
      </div>
    </Card>
  );
}

export function MemoryDetail({ r }: { r: AskResponse }) {
  const inc = r.memory?.incidents?.[0];
  if (!inc) return null;
  const patch = typeof inc.patch === "string" ? inc.patch : JSON.stringify(inc.patch, null, 2);
  return (
    <Card>
      <CardHeader icon={<History />} title={`Recalled ${inc.id}`} sub={`${new Date(inc.at).toLocaleString()} · depth ${inc.depth} · ${inc.exposed_count} exposed services`} />
      <div className="grid gap-5 p-5 md:grid-cols-2">
        <div className="divide-y divide-line self-start rounded-lg border border-line">
          <ShowMore limit={5} noun="services">
          {(inc.exposed || []).map((x) => (
            <div key={x.service} className="px-3.5 py-2.5">
              <p className="mb-1 text-[13px] font-medium text-ink">{x.service} <span className="font-mono text-[12px] font-normal text-ink-3">{x.advisory}</span></p>
              <PathChain via={x.path} requirement={x.requirement} exposed compact omitRoot />
            </div>
          ))}
          </ShowMore>
          {!inc.exposed?.length && inc.services && <p className="p-3 text-[13px] text-ink-2">{inc.services.join(", ")}</p>}
        </div>
        {patch && <CodeBlock label="Patch on file, per service" text={patch} maxH="max-h-72" />}
      </div>
    </Card>
  );
}

export function SpofPanel({ r }: { r: AskResponse }) {
  if (!r.spof) return null;
  const max = Math.max(...r.spof.maintainers.map((m) => m.reach), 1);
  return (
    <Card>
      <CardHeader icon={<Crown />} title="Single points of failure" sub="npm accounts ranked by how many fleet services they reach, ties broken by algo.pageRank." />
      <div className="grid gap-8 p-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div>
          <div className="mb-2 grid grid-cols-[24px_140px_minmax(0,1fr)_56px] gap-3 text-[12px] text-ink-3"><span>#</span><span>Maintainer</span><span>Fleet reach</span><span className="text-right">PageRank</span></div>
          <div className="space-y-1.5">
            {r.spof.maintainers.map((m, i) => (
              <motion.div key={m.maintainer} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.03, duration: 0.2 }}
                className="grid grid-cols-[24px_140px_minmax(0,1fr)_56px] items-center gap-3 text-[13px]">
                <span className="font-mono text-[12px] text-ink-3 tabular">{i + 1}</span>
                <span className="truncate font-mono text-ink">{m.maintainer}</span>
                <div className="flex items-center gap-2.5">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-subtle">
                    <motion.div initial={{ width: 0 }} animate={{ width: `${(m.reach / max) * 100}%` }} transition={{ duration: 0.6, delay: 0.1 + i * 0.03, ease: [0.22, 1, 0.36, 1] }}
                      className={clsx("h-full rounded-full", m.compromised_in_incident ? "bg-signal" : "bg-ink")} />
                  </div>
                  <span className="w-20 shrink-0 text-[12.5px] text-ink-2 tabular">{m.reach} services</span>
                </div>
                <span className="text-right font-mono text-[12px] text-ink-2 tabular">{m.pagerank.toFixed(2)}</span>
              </motion.div>
            ))}
          </div>
          {r.spof.maintainers.some((m) => m.compromised_in_incident) && (
            <p className="mt-4 flex items-center gap-2 text-[12.5px] text-ink-2"><span className="h-1.5 w-3 rounded-full bg-signal" />Phished on Sept 8, 2025 (curated incident data)</p>
          )}
        </div>
        <div>
          <p className="mb-2 text-[12px] text-ink-3">Most load-bearing packages, by algo.pageRank</p>
          <div className="divide-y divide-line rounded-lg border border-line">
            {r.spof.pagerank.map((p) => (
              <div key={p.pkg + p.version} className="flex items-center justify-between px-3 py-1.5 font-mono text-[12px]">
                <span className="truncate text-ink">{p.pkg}<span className="text-ink-3">@{p.version}</span></span>
                <span className="text-ink-2 tabular">{p.score.toFixed(3)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="border-t border-line p-5">
        <p className="mb-1 text-[12.5px] font-medium text-ink-2">Reach against PageRank</p>
        <SpofScatter spof={r.spof} />
      </div>
    </Card>
  );
}

export function PathPanel({ r }: { r: AskResponse }) {
  if (!r.path) return null;
  const n = r.path.via.length;
  return (
    <Card>
      <CardHeader icon={<GitBranch />} title={`Why is ${r.path.service} connected to ${r.path.package}?`} sub={`Shortest path with algo.SPpaths, ${r.path.hops} hops. The declared range is shown on every edge.`} />
      <ol className="p-5">
        {r.path.via.map((node, i) => (
          <motion.li key={node} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06, duration: 0.2 }} className="relative flex gap-4 pb-4 last:pb-0">
            {i < n - 1 && <span className="absolute left-[5px] top-4 h-[calc(100%-8px)] w-px bg-line-strong" />}
            <span className={clsx("relative mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full", i === 0 ? "bg-ink" : i === n - 1 ? "bg-signal" : "border-2 border-ink-3 bg-surface")} />
            <div className="min-w-0">
              <p className={clsx("font-mono text-[13.5px]", i === 0 || i === n - 1 ? "font-medium text-ink" : "text-ink-2")}>{node}</p>
              {i < n - 1 && <p className="mt-0.5 font-mono text-[12px] text-ink-3">requires {r.path!.requirements[i]}</p>}
            </div>
          </motion.li>
        ))}
      </ol>
    </Card>
  );
}

export function WakeOnly({ r }: { r: AskResponse }) {
  if (!r.maintainers) return null;
  return (
    <Card>
      <CardHeader icon={<Activity />} title={`Who maintains ${r.maintainers.pkg}`} sub="The npm registry today, plus curated publish rights at incident time." />
      <div className="flex flex-wrap gap-x-5 gap-y-2 p-5 text-[13.5px]">
        {r.maintainers.maintainers.map((m) => (
          <span key={m} className="flex items-center gap-2 font-mono text-ink">
            <span className={clsx("h-1.5 w-1.5 rounded-full", r.maintainers!.had_publish_rights.includes(m) ? "bg-signal" : "bg-ink-3")} />{m}
          </span>
        ))}
      </div>
    </Card>
  );
}

export function ErrorCard({ message, kind, onRetry }: { message: string; kind: string; onRetry: () => void }) {
  return (
    <Card className="border-signal/30 p-5">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-[18px] w-[18px] shrink-0 text-signal" />
        <div className="flex-1">
          <p className="text-[14px] font-medium text-ink">{kind === "graph_unavailable" ? "Graph unavailable" : kind === "network" ? "API unreachable" : "Something went wrong"}</p>
          <p className="mt-1 text-[13.5px] text-ink-2">{message}</p>
        </div>
        <Button onClick={onRetry}>Retry</Button>
      </div>
    </Card>
  );
}

export function ScopeStrip({ r }: { r: AskResponse }) {
  if (!r.scope.length) return null;
  return (
    <Card>
      <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.2fr)] gap-4 border-b border-line px-5 py-2.5 text-[12px] text-ink-3">
        <span>Package</span><span>Malicious, pin to</span><span className="hidden sm:block">Advisory</span>
      </div>
      <div className="divide-y divide-line">
        <ShowMore limit={8} noun="malicious versions">
        {r.scope.map((s) => (
          <div key={s.package + s.bad_version} className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.2fr)] items-center gap-4 px-5 py-2 font-mono text-[12.5px]">
            <span className="truncate text-ink">{s.package}</span>
            <span className="tabular"><span className="text-signal-ink line-through decoration-signal/40">{s.bad_version}</span>{s.clean_version ? <span className="text-ink-2"> → {s.clean_version}</span> : <span className="text-ink-3"> no clean version</span>}</span>
            <span className="hidden truncate text-ink-3 sm:block">{s.advisory_id ?? "user report"}</span>
          </div>
        ))}
        </ShowMore>
      </div>
    </Card>
  );
}

export function InsightsPanel({ r }: { r: AskResponse }) {
  const [tab, setTab] = useState<"rings" | "matrix">("rings");
  const reach = new Set([...r.exposed, ...r.safe].flatMap((e) => e.findings.map((f) => f.package))).size;
  const total = new Set((r.scope ?? []).map((s) => s.package)).size;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div>
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-ink">Insights</h3>
          <p className="mt-0.5 text-[13px] text-ink-2">
            <b className="font-semibold text-ink tabular">{reach}</b>{total > reach ? <> of <b className="font-semibold text-ink tabular">{total}</b></> : null} compromised package{(total || reach) === 1 ? "" : "s"} reachable from the fleet
            <span className="text-ink-3"> · {r.exposed.length + r.safe.length} services at depth {r.depth}</span>
          </p>
        </div>
        <Segmented value={tab} onChange={setTab} options={[{ value: "rings", label: "Blast radius" }, { value: "matrix", label: "Exposure matrix" }]} />
      </div>
      <div className="p-5">{tab === "rings" ? <BlastRings r={r} total={total} /> : <ExposureMatrix r={r} />}</div>
    </Card>
  );
}
