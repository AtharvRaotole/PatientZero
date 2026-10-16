"use client";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import {
  Activity, AlertTriangle, BellRing, Brain, ChevronDown, CircleCheck, CircleHelp, Crown, Database, FileCode2,
  GitBranch, History, RotateCcw, ShieldAlert, ShieldCheck, Terminal, Wand2,
} from "lucide-react";
import { useState } from "react";
import type { AskResponse, Fix, Incident, ServiceExposure, TraceStep, WakeEntry } from "@/lib/types";
import { Card, CardHeader, CopyButton, PathChain, Pill, Skeleton } from "./ui";
import { ApproveBar, ModePill } from "./agentui";
import type { Playbook } from "@/lib/types";

const OUTCOME: Record<string, { label: string; tone: "danger" | "safe" | "violet" | "warn"; icon: React.ReactNode }> = {
  answered_with_path: { label: "answered_with_path", tone: "danger", icon: <GitBranch className="h-3.5 w-3.5" /> },
  answered_from_memory: { label: "answered_from_memory", tone: "violet", icon: <Brain className="h-3.5 w-3.5" /> },
  not_in_graph: { label: "not_in_graph", tone: "safe", icon: <ShieldCheck className="h-3.5 w-3.5" /> },
  unknown_entity: { label: "unknown_entity", tone: "warn", icon: <CircleHelp className="h-3.5 w-3.5" /> },
};

export function OutcomeBanner({ r, onFresh }: { r: AskResponse; onFresh: () => void }) {
  const tokens = r.trace.reduce((a, s) => a + (Number((s as { tokens?: number }).tokens) || 0), 0);
  const o = OUTCOME[r.outcome];
  const neutral = r.outcome === "answered_with_path" && !r.exposed.length;
  return (
    <Card className={clsx("p-5", r.outcome === "answered_with_path" && r.exposed.length > 0 && "ring-glow-danger")}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={neutral ? "accent" : o.tone}>{o.icon}{o.label}</Pill>
        <Pill>intent: {r.intent}</Pill>
        {r.trigger && <Pill tone="accent">trigger: {r.trigger}</Pill>}
        <ModePill mode={r.mode} model={r.llm_model} />
        {r.mode === "llm" && <Pill tone="violet">{tokens} tokens</Pill>}
        <Pill>{r.total_ms.toFixed(0)} ms total</Pill>
      </div>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-3xl text-[15px] leading-relaxed text-white/85">{r.message}</p>
        {r.outcome === "answered_with_path" && r.exposed.length > 0 && (
          <div className="flex items-baseline gap-2">
            <span className="text-5xl font-semibold tracking-tight text-rose-300 tabular-nums">{r.exposed.length}</span>
            <span className="text-sm text-white/50">/ {r.fleet_size} services exposed</span>
          </div>
        )}
        {r.outcome === "answered_from_memory" && (
          <button onClick={onFresh} className="inline-flex items-center gap-2 rounded-xl bg-violet-500/15 px-3.5 py-2 text-sm font-medium text-violet-100 ring-1 ring-inset ring-violet-400/30 transition hover:bg-violet-500/25">
            <RotateCcw className="h-4 w-4" /> Run a fresh traversal
          </button>
        )}
      </div>
    </Card>
  );
}

function ServiceRow({ e, exposed, open, onToggle }: { e: ServiceExposure; exposed: boolean; open: boolean; onToggle: () => void }) {
  const main = e.findings.filter((f) => f.exposed === exposed || !exposed);
  return (
    <div className={clsx("rounded-xl border transition", exposed ? "border-rose-400/15 bg-rose-500/[0.04] hover:bg-rose-500/[0.07]" : "border-emerald-400/10 bg-emerald-500/[0.03] hover:bg-emerald-500/[0.06]")}>
      <button onClick={onToggle} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
        <div className="flex min-w-0 items-center gap-3">
          <span className={clsx("h-2 w-2 shrink-0 rounded-full", exposed ? "bg-rose-400 shadow-[0_0_12px_#ff4d6d]" : "bg-emerald-400")} />
          <span className="truncate font-medium text-white/90">{e.service}</span>
          <Pill className="hidden sm:inline-flex">tier {e.tier}</Pill>
          <span className="hidden text-xs text-white/40 md:inline">{e.team}</span>
        </div>
        <div className="flex items-center gap-2">
          {exposed ? (
            <span className="text-xs text-rose-200/80">{e.exposed_packages.join(", ")}</span>
          ) : (
            <span className="text-xs text-emerald-200/70">safe by range</span>
          )}
          <span className="text-[11px] text-white/35">{main[0].path.hops} hop{main[0].path.hops > 1 ? "s" : ""}</span>
          <ChevronDown className={clsx("h-4 w-4 text-white/40 transition", open && "rotate-180")} />
        </div>
      </button>
      <div className="px-4 pb-3">
        <PathChain via={main[0].path.via} requirement={main[0].path.requirement} bad={main[0].bad_version} exposed={main[0].path.exposed} compact={!open} />
        {!exposed && <p className="mt-1.5 text-[11.5px] text-emerald-200/70">{main[0].path.reason}</p>}
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-3 border-t border-white/[0.06] px-4 py-3">
              {e.findings.map((f, i) => (
                <div key={i} className="space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-white/85">{f.package}@{f.bad_version}</span>
                    {f.advisory_id && <Pill tone={f.exposed ? "danger" : "neutral"}>{f.advisory_id}</Pill>}
                    {f.ghsa && <Pill>{f.ghsa}</Pill>}
                    <span className="text-white/40">{f.path_count} path{f.path_count > 1 ? "s" : ""} in graph</span>
                  </div>
                  <PathChain via={f.path.via} requirement={f.path.requirement} bad={f.bad_version} exposed={f.path.exposed} />
                  <p className={clsx("text-[11.5px]", f.exposed ? "text-rose-200/70" : "text-emerald-200/70")}>{f.path.reason}</p>
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

export function ExposurePanel({ r, depth, onDepth, onSelect }: { r: AskResponse; depth: number; onDepth: (d: number) => void; onSelect: (svc: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [tab, setTab] = useState<"exposed" | "safe">("exposed");
  const list = tab === "exposed" ? r.exposed : r.safe;
  return (
    <Card delay={0.05}>
      <CardHeader
        icon={<ShieldAlert className="h-4 w-4" />}
        title="Blast radius"
        sub="Exposed only if the range on the last edge admits the malicious version (npm semver)"
        right={
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-white/45">depth</span>
            <input aria-label="depth" type="range" min={1} max={6} value={depth} onChange={(e) => onDepth(Number(e.target.value))} className="depth w-28" />
            <span className="w-4 font-mono text-sm text-white/80 tabular-nums">{depth}</span>
          </div>
        }
      />
      <div className="flex gap-1 px-5 pt-3">
        {(["exposed", "safe"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={clsx("rounded-lg px-3 py-1.5 text-xs font-medium transition", tab === t ? "bg-white/10 text-white" : "text-white/50 hover:text-white/80")}>
            {t === "exposed" ? `Exposed (${r.exposed.length})` : `Not exposed, with reason (${r.safe.length})`}
          </button>
        ))}
        <span className="ml-auto self-center text-[11px] text-white/35">{r.clean.length} services never reach an affected package</span>
      </div>
      <div className="scroll-thin max-h-[520px] space-y-2 overflow-y-auto p-5 pt-3">
        {list.length === 0 && (
          <div className="grid place-items-center rounded-xl border border-dashed border-white/10 py-10 text-center text-sm text-white/45">
            {tab === "exposed" ? `No fleet path admits a malicious version at depth ${depth}.` : "No services reach an affected package with a non-admitting range."}
          </div>
        )}
        {list.map((e) => (
          <ServiceRow key={e.service} e={e} exposed={tab === "exposed"} open={open === e.service}
            onToggle={() => { setOpen(open === e.service ? null : e.service); onSelect(e.service); }} />
        ))}
      </div>
    </Card>
  );
}

export function TracePanel({ trace, loading }: { trace: TraceStep[]; loading: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Card delay={0.1}>
      <CardHeader icon={<Terminal className="h-4 w-4" />} title="Agent trace" sub="Fixed tool catalogue. Human-written Cypher from queries/*.cypher" right={<Pill tone="accent"><Database className="h-3 w-3" />FalkorDB</Pill>} />
      <div className="scroll-thin max-h-[440px] space-y-1.5 overflow-y-auto p-4">
        {loading && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}
        {!loading && trace.length === 0 && <p className="py-6 text-center text-sm text-white/40">Tool calls appear here.</p>}
        {!loading && trace.map((s, i) => !s.cypher ? (
          <motion.div key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.07 }}
            className={clsx("flex items-center gap-3 rounded-xl border px-3 py-2", s.tool === "llm" ? "border-violet-400/20 bg-violet-500/[0.06]" : "border-white/[0.05] bg-white/[0.02]", s.error && "border-rose-400/30")}>
            <span className={clsx("grid h-5 w-5 place-items-center rounded-md font-mono text-[10px]", s.tool === "llm" ? "bg-violet-500/20 text-violet-200" : "bg-white/[0.06] text-white/50")}>{s.tool === "llm" ? "AI" : i + 1}</span>
            <span className={clsx("font-mono text-[12px]", s.tool === "llm" ? "text-violet-200" : "text-white/60")}>{s.tool}</span>
            <span className="truncate text-[11.5px] text-white/50">{s.error ? `${s.note ?? ""} ${s.error}` : s.note}</span>
            {s.ms > 0 && <span className="ml-auto shrink-0 font-mono text-[11px] text-white/40">{s.ms.toFixed(0)} ms</span>}
          </motion.div>
        ) : (
          <motion.div key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.07 }}
            className={clsx("rounded-xl border bg-black/20", s.error ? "border-rose-400/30" : "border-white/[0.06]")}>
            <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center gap-3 px-3 py-2 text-left">
              <span className="grid h-5 w-5 place-items-center rounded-md bg-white/[0.06] font-mono text-[10px] text-white/60">{i + 1}</span>
              <span className="font-mono text-[12.5px] text-indigo-200">{s.tool}</span>
              {s.query && s.query !== s.tool && <span className="hidden truncate font-mono text-[11px] text-white/35 sm:inline">.{s.query}</span>}
              {s.write && <Pill tone="warn">write</Pill>}
              <span className="ml-auto flex items-center gap-3 font-mono text-[11px] tabular-nums">
                <span className="text-white/45">{s.rows} rows</span>
                <span className={clsx(s.ms > 100 ? "text-amber-300" : "text-emerald-300")}>{s.ms.toFixed(1)} ms</span>
              </span>
            </button>
            <AnimatePresence initial={false}>
              {open === i && (
                <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} className="overflow-hidden">
                  <div className="space-y-2 border-t border-white/[0.06] p-3">
                    {s.note && <p className="text-[11px] text-white/50">{s.note}</p>}
                    {s.error && <p className="text-[11px] text-rose-300">{s.error}</p>}
                    {s.cypher && <pre className="scroll-thin overflow-x-auto rounded-lg bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-sky-100/85">{s.cypher}</pre>}
                    <pre className="scroll-thin overflow-x-auto rounded-lg bg-black/30 p-2 font-mono text-[10.5px] text-white/55">params: {JSON.stringify(s.params)}</pre>
                    {s.file && <p className="font-mono text-[10.5px] text-white/35">{s.file}</p>}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        ))}
      </div>
    </Card>
  );
}

export function WakeCard({ wake }: { wake: WakeEntry[] }) {
  if (!wake.length) return null;
  return (
    <Card delay={0.15}>
      <CardHeader icon={<BellRing className="h-4 w-4" />} title="Who to wake" sub={`${wake.length} owning team${wake.length > 1 ? "s" : ""}, synthetic Acme org`} />
      <div className="scroll-thin max-h-[320px] divide-y divide-white/[0.05] overflow-y-auto">
        {wake.map((w) => (
          <div key={w.service} className="flex items-center justify-between gap-3 px-5 py-2.5">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-white/90">{w.service}</p>
              <p className="text-[11px] text-white/40">{w.team_display} · {w.slack}</p>
            </div>
            <div className="text-right">
              {w.oncall.length ? w.oncall.map((o) => (
                <div key={o.handle} className="flex items-center gap-2">
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 text-[10px] font-semibold text-white">{o.name.split(" ").map((p) => p[0]).join("")}</span>
                  <span className="text-xs text-white/80">{o.name} <span className="text-white/40">{o.handle}</span></span>
                </div>
              )) : <span className="text-xs text-white/40">no on-call</span>}
              <span className="mt-0.5 block text-[10px] uppercase tracking-wider text-white/30">tier {w.tier} · pager {w.pager}</span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function FixPanel({ canFix, fix, loading, onFix, onApproved }: { canFix: boolean; fix: Fix | null; loading: boolean; onFix: () => void; onApproved: (p: Playbook) => void }) {
  const [sel, setSel] = useState(0);
  if (!canFix && !fix) return null;
  const s = fix?.per_service[sel];
  return (
    <Card delay={0.2}>
      <CardHeader icon={<Wand2 className="h-4 w-4" />} title="Remediation" sub="npm overrides + notification drafts. Drafts only, nothing is sent"
        right={
          <button onClick={onFix} disabled={loading} className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-rose-500 to-fuchsia-500 px-3.5 py-1.5 text-xs font-semibold text-white shadow-[0_8px_30px_-8px_rgba(255,77,109,0.7)] transition hover:brightness-110 disabled:opacity-50">
            <Wand2 className="h-3.5 w-3.5" /> {loading ? "Generating..." : fix ? "Regenerate fix" : "Generate fix"}
          </button>
        } />
      {!fix && !loading && <p className="px-5 py-6 text-sm text-white/45">Builds a per-service overrides patch with clean versions from the advisories, drafts a page to each owner, and writes the Incident back to FalkorDB as memory.</p>}
      {loading && <div className="space-y-2 p-5"><Skeleton className="h-6 w-1/2" /><Skeleton className="h-28" /></div>}
      {fix && s && (
        <div className="space-y-4 p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Pill tone="violet"><Brain className="h-3 w-3" /> {fix.incident_id}</Pill>
            <Pill tone="safe">{fix.exposed_edges} EXPOSED edges written</Pill>
          </div>
          <ApproveBar key={fix.incident_id} incidentId={fix.incident_id} onApproved={onApproved} />
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wider text-white/40">Fleet-wide overrides</span>
              <CopyButton text={fix.combined_json} />
            </div>
            <pre className="scroll-thin overflow-x-auto rounded-xl bg-black/40 p-3 font-mono text-[11.5px] text-emerald-100/90">{fix.combined_json}</pre>
          </div>
          <div className="scroll-thin flex gap-1.5 overflow-x-auto pb-1">
            {fix.per_service.map((p, i) => (
              <button key={p.service} onClick={() => setSel(i)} className={clsx("shrink-0 rounded-lg px-2.5 py-1 text-[11px] font-medium transition", i === sel ? "bg-white/12 text-white ring-1 ring-white/20" : "text-white/50 hover:text-white/80")}>{p.service}</button>
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <div className="mb-1.5 flex items-center justify-between"><span className="text-[11px] font-medium uppercase tracking-wider text-white/40">{s.service} package.json</span><CopyButton text={s.overrides_json} /></div>
              <pre className="scroll-thin overflow-x-auto rounded-xl bg-black/40 p-3 font-mono text-[11.5px] text-emerald-100/90">{s.overrides_json}</pre>
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between"><span className="text-[11px] font-medium uppercase tracking-wider text-white/40">Draft to {s.slack}</span><CopyButton text={s.draft} label="Copy draft" /></div>
              <pre className="scroll-thin max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/75">{s.draft}</pre>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

export function MemoryPanel({ incidents, onRecall, onReset }: { incidents: Incident[] | null; onRecall: (q: string) => void; onReset: () => void }) {
  return (
    <Card delay={0.25}>
      <CardHeader icon={<History className="h-4 w-4" />} title="Incident memory" sub="Incident + Finding nodes read back from the graph"
        right={incidents && incidents.length > 0 ? <button onClick={onReset} className="text-[11px] text-white/40 transition hover:text-rose-300">Reset demo memory</button> : null} />
      <div className="scroll-thin max-h-64 space-y-1.5 overflow-y-auto p-4">
        {incidents === null && [0, 1].map((i) => <Skeleton key={i} className="h-12" />)}
        {incidents?.length === 0 && <p className="py-5 text-center text-sm text-white/40">No incidents stored yet. Generate a fix to write one.</p>}
        {incidents?.map((i) => (
          <button key={i.id} onClick={() => onRecall(i.entity_key || i.trigger)} className="flex w-full items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-black/20 px-3 py-2 text-left transition hover:border-violet-400/30 hover:bg-violet-500/[0.06]">
            <div className="min-w-0">
              <p className="truncate font-mono text-[11.5px] text-violet-200">{i.id}</p>
              <p className="truncate text-[11px] text-white/45">{i.trigger}</p>
            </div>
            <div className="shrink-0 text-right">
              <p className="text-sm font-semibold text-rose-300 tabular-nums">{i.exposed_count}</p>
              <p className="text-[10px] text-white/35">{new Date(i.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</p>
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
    <Card delay={0.05}>
      <CardHeader icon={<Brain className="h-4 w-4" />} title={`Recalled: ${inc.id}`} sub={`${new Date(inc.at).toLocaleString()} · depth ${inc.depth} · ${inc.exposed_count} exposed services`} />
      <div className="grid gap-4 p-5 md:grid-cols-2">
        <div className="scroll-thin max-h-72 space-y-2 overflow-y-auto">
          {(inc.exposed || []).map((x) => (
            <div key={x.service} className="rounded-xl border border-rose-400/10 bg-rose-500/[0.03] p-2.5">
              <p className="mb-1 text-sm font-medium text-white/85">{x.service} <span className="text-[11px] text-white/40">{x.advisory}</span></p>
              <PathChain via={x.path} requirement={x.requirement} exposed compact />
            </div>
          ))}
          {!inc.exposed?.length && inc.services && <p className="text-sm text-white/60">{inc.services.join(", ")}</p>}
        </div>
        {patch && (
          <div>
            <div className="mb-1.5 flex items-center justify-between"><span className="text-[11px] font-medium uppercase tracking-wider text-white/40">Patch on file (per service)</span><CopyButton text={patch} /></div>
            <pre className="scroll-thin max-h-64 overflow-auto rounded-xl bg-black/40 p-3 font-mono text-[11px] text-emerald-100/85">{patch}</pre>
          </div>
        )}
      </div>
    </Card>
  );
}

export function SpofPanel({ r }: { r: AskResponse }) {
  if (!r.spof) return null;
  const max = Math.max(...r.spof.maintainers.map((m) => m.reach), 1);
  return (
    <Card delay={0.05}>
      <CardHeader icon={<Crown className="h-4 w-4" />} title="Single points of failure" sub="Maintainer accounts ranked by fleet reach, tie-broken by algo.pageRank" />
      <div className="grid gap-6 p-5 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-2">
          {r.spof.maintainers.map((m, i) => (
            <motion.div key={m.maintainer} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }} className="flex items-center gap-3">
              <span className="w-5 text-right font-mono text-xs text-white/40">{i + 1}</span>
              <span className="w-36 truncate font-mono text-[12.5px] text-white/85">{m.maintainer}</span>
              <div className="relative h-6 flex-1 overflow-hidden rounded-lg bg-white/[0.04]">
                <motion.div initial={{ width: 0 }} animate={{ width: `${(m.reach / max) * 100}%` }} transition={{ duration: 0.8, delay: i * 0.05 }}
                  className={clsx("h-full rounded-lg", m.compromised_in_incident ? "bg-gradient-to-r from-rose-500/70 to-fuchsia-500/60" : "bg-gradient-to-r from-indigo-500/60 to-violet-500/50")} />
                <span className="absolute inset-y-0 left-2 flex items-center text-[11px] font-medium text-white/90">{m.reach} services</span>
              </div>
              <span className="w-20 text-right font-mono text-[10.5px] text-white/40">pr {m.pagerank.toFixed(2)}</span>
              {m.compromised_in_incident && <Pill tone="danger">phished 2025-09-08</Pill>}
            </motion.div>
          ))}
        </div>
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-white/40">Most load-bearing packages (algo.pageRank)</p>
          <div className="space-y-1">
            {r.spof.pagerank.map((p) => (
              <div key={p.pkg + p.version} className="flex items-center justify-between rounded-lg bg-white/[0.03] px-3 py-1.5 font-mono text-[11.5px]">
                <span className="text-white/80">{p.pkg}<span className="text-white/35">@{p.version}</span></span>
                <span className="text-indigo-200/80">{p.score.toFixed(3)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

export function PathPanel({ r }: { r: AskResponse }) {
  if (!r.path) return null;
  return (
    <Card delay={0.05}>
      <CardHeader icon={<GitBranch className="h-4 w-4" />} title={`Why is ${r.path.service} connected to ${r.path.package}?`} sub={`Shortest path via algo.SPpaths, ${r.path.hops} hops`} />
      <div className="flex flex-wrap items-center gap-2 p-5">
        {r.path.via.map((n, i) => (
          <motion.div key={n} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: i * 0.12 }} className="flex items-center gap-2">
            {i > 0 && (
              <span className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-violet-300/80">{r.path!.requirements[i - 1]}</span>
                <span className="text-white/30">→</span>
              </span>
            )}
            <span className={clsx("rounded-xl px-3 py-2 font-mono text-[12.5px] ring-1 ring-inset", i === 0 ? "bg-indigo-500/15 text-indigo-100 ring-indigo-400/30" : i === r.path!.via.length - 1 ? "bg-violet-500/20 text-violet-100 ring-violet-400/40" : "bg-white/[0.05] text-white/80 ring-white/10")}>{n}</span>
          </motion.div>
        ))}
      </div>
    </Card>
  );
}

export function WakeOnly({ r }: { r: AskResponse }) {
  if (r.maintainers) {
    return (
      <Card delay={0.05}>
        <CardHeader icon={<Activity className="h-4 w-4" />} title={`Who maintains ${r.maintainers.pkg}`} sub="npm registry today, plus curated publish rights at incident time" />
        <div className="flex flex-wrap gap-2 p-5">
          {r.maintainers.maintainers.map((m) => <Pill key={m} tone={r.maintainers!.had_publish_rights.includes(m) ? "danger" : "accent"}>{m}</Pill>)}
        </div>
      </Card>
    );
  }
  return null;
}

export function ErrorCard({ message, kind, onRetry }: { message: string; kind: string; onRetry: () => void }) {
  return (
    <Card className="border-rose-400/30 p-5">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
        <div className="flex-1">
          <p className="font-medium text-rose-100">{kind === "graph_unavailable" ? "Graph unavailable" : kind === "network" ? "API unreachable" : "Something went wrong"}</p>
          <p className="mt-1 text-sm text-white/60">{message}</p>
        </div>
        <button onClick={onRetry} className="rounded-lg bg-white/[0.06] px-3 py-1.5 text-xs text-white/80 ring-1 ring-white/10 hover:bg-white/10">Retry</button>
      </div>
    </Card>
  );
}

export function ScopeStrip({ r }: { r: AskResponse }) {
  if (!r.scope.length) return null;
  return (
    <div className="scroll-thin flex gap-1.5 overflow-x-auto pb-1">
      {r.scope.map((s) => (
        <span key={s.package + s.bad_version} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-rose-500/[0.07] px-2 py-1 font-mono text-[11px] text-rose-200/90 ring-1 ring-inset ring-rose-400/15">
          <FileCode2 className="h-3 w-3 opacity-60" />{s.package}@{s.bad_version}
          {s.clean_version && <span className="text-emerald-300/80">→ {s.clean_version}</span>}
        </span>
      ))}
    </div>
  );
}

export { CircleCheck };
