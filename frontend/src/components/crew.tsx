"use client";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { Check, ChevronDown, FastForward, Link2, RotateCcw, Send, Users, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { API_URL, ApiError, apiGet, handle, withTenant } from "@/lib/api";
import { getTenant } from "@/lib/tenant";
import type { CrewAgentKey, CrewFinding, CrewRun, Followup } from "@/lib/types";
import { CopyButton } from "./ui";
import { FollowupLine } from "./session";

const EASE = [0.22, 1, 0.36, 1] as const;
const AGENTS: CrewAgentKey[] = ["triage", "investigator", "responder", "fixer"];
const META: Record<CrewAgentKey, { title: string; role: string }> = {
  triage: { title: "Triage", role: "Resolve the trigger and size the blast radius" },
  investigator: { title: "Investigator", role: "Explain the paths, the honest negatives and the single points of failure" },
  responder: { title: "Responder", role: "Decide who to wake and draft the pages" },
  fixer: { title: "Fixer", role: "Build the patch plan and reuse approved playbooks" },
};
const DAG: [CrewAgentKey, CrewAgentKey][] = [["triage", "investigator"], ["triage", "responder"], ["investigator", "fixer"], ["responder", "fixer"]];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ev = { event: string; data: any };
type Status = "waiting" | "running" | "done" | "no_incident" | "error";
type Read = { id: string; agent: CrewAgentKey; kind: string };

/** Reading speed: the real run takes about 100 ms, so events are played back with these gaps (ms). */
const GAP: Record<string, number> = { followup: 250, run: 300, agent_start: 420, incident: 320, finding: 650, read: 820, handoff: 560, agent_done: 380, done: 250, error: 0 };

export type CrewSpec = { key: number; mode: "live"; query: string; depth: number; sessionId: string } | { key: number; mode: "replay"; runId: string };

// ------------------------------------------------------------------ transport

/** POST /api/crew/run and parse the text/event-stream body as it arrives. */
async function streamCrew(body: object, onEvent: (e: Ev) => void, signal: AbortSignal) {
  const res = await fetch(`${API_URL}${withTenant("/api/crew/run")}`, {
    method: "POST", signal,
    headers: { "content-type": "application/json", accept: "text/event-stream", "X-Tenant": getTenant() },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) await handle(res);
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent({ event, data: JSON.parse(data) });
    }
  }
}

/** A stored run (GET /api/crew/{id}) back into the event sequence, in graph `seq` order. Reads are what
 *  crew_read_upstream returns: every finding upstream along HANDED_OFF_TO. */
function replayEvents(r: CrewRun): Ev[] {
  const by = Object.fromEntries(r.agents.map((a) => [a.agent, a])) as Record<CrewAgentKey, CrewRun["agents"][number]>;
  const items: { seq: number; ev: Ev }[] = [];
  const upstream = (a: CrewAgentKey): CrewAgentKey[] => {
    const direct = DAG.filter(([, to]) => to === a).map(([f]) => f);
    return Array.from(new Set(direct.flatMap((d) => [...upstream(d), d])));
  };
  for (const a of AGENTS) {
    const ag = by[a];
    if (!ag) continue;
    const fs = ag.findings ?? [];
    const inSeq = r.handoffs.filter((h) => h.to_agent === a).map((h) => h.seq);
    const start = inSeq.length ? Math.max(...inSeq) + 0.1 : 0;
    const firstF = fs.length ? Math.min(...fs.map((f) => f.seq ?? 0)) : start + 0.5;
    const outSeq = r.handoffs.filter((h) => h.from_agent === a).map((h) => h.seq);
    const end = outSeq.length ? Math.min(...outSeq) - 0.1 : (fs.length ? Math.max(...fs.map((f) => f.seq ?? 0)) + 0.1 : start + 1);
    // the second parallel agent starts right after the first, as in the live stream
    items.push({ seq: Math.min(start, firstF - 0.3), ev: { event: "agent_start", data: { agent: a, title: ag.title, role: ag.role } } });
    if (a !== "triage") {
      const reads = upstream(a).flatMap((u) => (by[u]?.findings ?? []).map((f) => ({ id: f.id, agent: u, kind: f.kind })));
      items.push({ seq: Math.min(start, firstF - 0.3) + 0.05, ev: { event: "read", data: { agent: a, query: "crew_read_upstream", findings: reads } } });
    }
    for (const f of fs) items.push({ seq: f.seq ?? 0, ev: { event: "finding", data: { agent: a, finding: { ...f, agent: a } } } });
    items.push({ seq: end, ev: { event: "agent_done", data: { agent: a, status: ag.status, ms: ag.ms, summary: ag.summary, error: ag.error } } });
    if (a === "triage" && r.incident) items.push({ seq: 0.5, ev: { event: "incident", data: { incident_id: r.incident.id, created: r.incident.triage_count === 1, triage_count: r.incident.triage_count } } });
  }
  for (const h of r.handoffs) items.push({ seq: h.seq, ev: { event: "handoff", data: { from: h.from_agent, to: h.to_agent, reason: h.reason } } });
  items.sort((x, y) => x.seq - y.seq);
  return [
    { event: "run", data: { run_id: r.run_id, tenant: r.tenant, company: r.company, query: r.query, depth: r.depth, session_id: r.session_id, replay: true } },
    ...items.map((i) => i.ev),
    { event: "done", data: r },
  ];
}

// ------------------------------------------------------------------ state from played events

type LaneState = { status: Status; ms?: number; summary?: string; findings: CrewFinding[]; reads: Read[]; startIdx?: number; endIdx?: number };
type View = {
  runId?: string; company?: string; query?: string; depth?: number; replay?: boolean; followup?: Followup;
  lanes: Record<CrewAgentKey, LaneState>; handoffs: { from: CrewAgentKey; to: CrewAgentKey; reason: string; idx: number }[];
  incident?: { incident_id: string; created: boolean; triage_count: number }; done?: CrewRun; error?: string; log: { idx: number; who: string; text: string }[];
};

function reduce(events: Ev[]): View {
  const lanes = Object.fromEntries(AGENTS.map((a) => [a, { status: "waiting", findings: [], reads: [] } as LaneState])) as Record<CrewAgentKey, LaneState>;
  const v: View = { lanes, handoffs: [], log: [] };
  events.forEach(({ event, data }, idx) => {
    const L = data?.agent ? lanes[data.agent as CrewAgentKey] : undefined;
    switch (event) {
      case "followup": v.followup = data; break;
      case "run": Object.assign(v, { runId: data.run_id, company: data.company, query: data.query, depth: data.depth, replay: !!data.replay }); v.log.push({ idx, who: "crew", text: `run ${data.run_id} on ${data.company}` }); break;
      case "agent_start": if (L) { L.status = "running"; L.startIdx = idx; } v.log.push({ idx, who: data.agent, text: "started" }); break;
      case "incident": v.incident = data; v.log.push({ idx, who: "triage", text: `${data.created ? "created" : "updated"} ${data.incident_id}` }); break;
      case "finding": if (L) L.findings.push(data.finding); v.log.push({ idx, who: data.agent, text: `wrote ${data.finding.kind}` }); break;
      case "read": if (L) L.reads.push(...data.findings); v.log.push({ idx, who: data.agent, text: `read ${data.findings.length} findings from the graph` }); break;
      case "agent_done": if (L) { L.status = data.status === "done" ? "done" : data.status; L.ms = data.ms; L.summary = data.summary; L.endIdx = idx; } v.log.push({ idx, who: data.agent, text: `${data.status} in ${Number(data.ms).toFixed(1)} ms` }); break;
      case "handoff": v.handoffs.push({ from: data.from, to: data.to, reason: data.reason, idx }); v.log.push({ idx, who: data.from, text: `handed off to ${data.to}` }); break;
      case "error": v.error = data.message; break;
      case "done": v.done = data; break;
    }
  });
  return v;
}

// ------------------------------------------------------------------ the view

export function CrewView({ spec, onClose, onRunId, onFinished, onRestart }: {
  spec: CrewSpec; onClose: () => void; onRunId: (id: string) => void; onFinished: (r: CrewRun) => void; onRestart: () => void;
}) {
  const [played, setPlayed] = useState<Ev[]>([]);
  const queue = useRef<Ev[]>([]);
  const [received, setReceived] = useState(0);
  const [streamDone, setStreamDone] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);
  const [fast, setFast] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finished = useRef(false);

  // load: live stream or stored replay
  useEffect(() => {
    queue.current = []; setPlayed([]); setReceived(0); setStreamDone(false); setFatal(null); setFast(false); finished.current = false;
    const ctl = new AbortController();
    const push = (e: Ev) => { queue.current.push(e); setReceived((n) => n + 1); if (e.event === "run") onRunId(e.data.run_id); };
    (async () => {
      try {
        if (spec.mode === "live") {
          await streamCrew({ query: spec.query, depth: spec.depth, session_id: spec.sessionId || undefined, tenant: getTenant() }, push, ctl.signal);
        } else {
          const r = await apiGet<CrewRun>(`/api/crew/${encodeURIComponent(spec.runId)}`);
          replayEvents(r).forEach(push);
        }
      } catch (e) {
        if (ctl.signal.aborted) return;
        const a = e as ApiError;
        setFatal(a.status === 404 ? "That crew run is not in this tenant's graph." : a.message || "The crew stream failed.");
      } finally {
        if (!ctl.signal.aborted) setStreamDone(true);
      }
    })();
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec.key]);

  // player: one event at a time, at reading speed (or all at once)
  useEffect(() => {
    if (timer.current) return;
    const step = () => {
      timer.current = null;
      const next = queue.current.shift();
      if (!next) return;
      setPlayed((p) => [...p, next]);
      const gap = fast ? 0 : GAP[next.event] ?? 300;
      timer.current = setTimeout(step, gap);
    };
    if (queue.current.length) timer.current = setTimeout(step, played.length ? 0 : 150);
  }, [received, fast, played.length]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const v = useMemo(() => reduce(played), [played]);
  const complete = !!v.done || (!!v.error && streamDone && queue.current.length === 0);
  useEffect(() => { if (v.done && !finished.current) { finished.current = true; onFinished(v.done); } }, [v.done, onFinished]);

  const skip = () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    setFast(true);
    setPlayed((p) => [...p, ...queue.current.splice(0)]);
  };

  const parallel = v.lanes.investigator.status === "running" && v.lanes.responder.status === "running";
  const realMs = v.done?.ms;
  const status: "running" | "done" | "no_incident" | "error" = fatal || v.error ? "error" : v.done ? (v.done.status === "done" ? "done" : "no_incident") : "running";

  return (
    <motion.section id="crew" data-testid="crew-view" data-status={status} data-run={v.runId ?? ""}
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, ease: EASE }}
      className="scroll-mt-[72px] overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-ink text-paper"><Users className="h-4 w-4" /></span>
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-x-2 text-[14px] font-semibold tracking-[-0.01em] text-ink">
              Response crew
              {v.query && <span className="truncate font-mono text-[13px] font-normal text-ink-2">{v.query}</span>}
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-ink-3">
              {v.company && <span>{v.company}</span>}
              {v.depth && <><span className="text-line-strong">/</span><span>depth {v.depth}</span></>}
              {v.runId && <><span className="text-line-strong">/</span><span className="font-mono">{v.runId}</span></>}
              {v.replay && <><span className="text-line-strong">/</span><span>replayed from the graph</span></>}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusPill status={status} ms={realMs} />
          {!complete && !fatal && <button type="button" onClick={skip} className="press inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium text-ink-2 hover:bg-subtle hover:text-ink"><FastForward className="h-3.5 w-3.5" />Skip to end</button>}
          {complete && v.runId && <ShareLink runId={v.runId} />}
          {complete && <button type="button" onClick={onRestart} className="press inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium text-ink-2 hover:bg-subtle hover:text-ink"><RotateCcw className="h-3.5 w-3.5" />Run again</button>}
          <button type="button" onClick={onClose} aria-label="Close the crew view" className="press grid h-8 w-8 place-items-center rounded-lg text-ink-3 hover:bg-subtle hover:text-ink"><X className="h-4 w-4" /></button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-paper/60 px-5 py-2 text-[12px] text-ink-3">
        <span>Four agents. Every handoff goes through FalkorDB: each agent reads its inputs back from the graph.</span>
        <span className="ml-auto tabular">{realMs !== undefined ? <>Actual run <span className="font-mono text-ink-2">{realMs.toFixed(1)} ms</span>, shown at reading speed</> : v.replay ? "Replaying" : "Streaming"}</span>
      </div>
      {v.followup && <div className="px-5 pt-1"><FollowupLine f={v.followup} /></div>}

      {fatal ? (
        <p className="px-5 py-8 text-[14px] text-ink-2">{fatal}</p>
      ) : (
        <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_330px]">
          <Lanes v={v} parallel={parallel} />
          <div className="border-t border-line lg:border-l lg:border-t-0">
            <Dag v={v} />
            <Timeline v={v} total={Math.max(played.length, 1)} />
            <EventLog v={v} />
          </div>
        </div>
      )}
      <AnimatePresence>{v.done && v.done.status === "done" && <Summary r={v.done} />}</AnimatePresence>
      {v.done && v.done.status !== "done" && (
        <p className="border-t border-line px-5 py-4 text-[14px] text-ink-2">{v.done.message || "Triage stopped: the trigger is not in the graph, so nothing was handed off."}</p>
      )}
      {v.error && <p className="border-t border-line px-5 py-4 text-[14px] text-signal-ink">{v.error}</p>}
    </motion.section>
  );
}

function StatusPill({ status, ms }: { status: "running" | "done" | "no_incident" | "error"; ms?: number }) {
  const label = status === "running" ? "Running" : status === "done" ? `Done${ms !== undefined ? ` in ${ms.toFixed(0)} ms` : ""}` : status === "no_incident" ? "No incident" : "Failed";
  return (
    <span data-testid="crew-status" className="mr-1 inline-flex h-7 items-center gap-2 rounded-full px-2.5 text-[12px] font-medium text-ink-2 ring-1 ring-inset ring-line">
      <span className={clsx("h-1.5 w-1.5 rounded-full", status === "running" ? "animate-pulse bg-ink" : status === "done" ? "bg-safe" : status === "error" ? "bg-signal" : "bg-ink-3")} />
      {label}
    </span>
  );
}

function ShareLink({ runId }: { runId: string }) {
  const [done, setDone] = useState(false);
  const copy = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("run", runId);
    navigator.clipboard?.writeText(url.toString()).then(() => { setDone(true); setTimeout(() => setDone(false), 1400); }, () => {});
  };
  return (
    <button type="button" onClick={copy} className="press inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium text-ink-2 hover:bg-subtle hover:text-ink">
      {done ? <Check className="h-3.5 w-3.5 text-safe" /> : <Link2 className="h-3.5 w-3.5" />}{done ? "Link copied" : "Share replay"}
    </button>
  );
}

// ------------------------------------------------------------------ lanes, with read pulls

type Ghost = { key: string; label: string; from: DOMRect; to: DOMRect; base: DOMRect };

function Lanes({ v, parallel }: { v: View; parallel: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [ghosts, setGhosts] = useState<Ghost[]>([]);
  const [pulse, setPulse] = useState<Set<string>>(new Set());
  const seenReads = useRef(0);
  const allReads = AGENTS.flatMap((a) => v.lanes[a].reads.map((r) => ({ ...r, to: a })));

  // a read event: each finding it read travels from its card to the reading lane
  useLayoutEffect(() => {
    if (!box.current || allReads.length <= seenReads.current) { seenReads.current = Math.min(seenReads.current, allReads.length); return; }
    const fresh = allReads.slice(seenReads.current);
    seenReads.current = allReads.length;
    const base = box.current.getBoundingClientRect();
    const gs: Ghost[] = [];
    fresh.forEach((r, i) => {
      const src = box.current!.querySelector(`[data-fid="${CSS.escape(r.id)}"]`);
      const dst = box.current!.querySelector(`[data-reads="${r.to}"]`);
      if (src && dst) gs.push({ key: `${r.to}-${r.id}-${Date.now()}-${i}`, label: r.kind, from: src.getBoundingClientRect(), to: dst.getBoundingClientRect(), base });
    });
    if (!gs.length) return;
    setGhosts((g) => [...g, ...gs]);
    setPulse(new Set(fresh.map((r) => r.id)));
    const t = setTimeout(() => { setGhosts((g) => g.filter((x) => !gs.includes(x))); setPulse(new Set()); }, 900);
    return () => clearTimeout(t);
  }, [allReads.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={box} className="relative divide-y divide-line">
      {AGENTS.map((a) => (
        <Lane key={a} agent={a} s={v.lanes[a]} pulse={pulse} parallelTag={parallel && (a === "investigator" || a === "responder")} />
      ))}
      {ghosts.map((g, i) => (
        <motion.span key={g.key} className="pointer-events-none absolute z-10 rounded-md bg-ink px-1.5 py-0.5 font-mono text-[11px] text-paper shadow-md"
          initial={{ left: g.from.left - g.base.left + 8, top: g.from.top - g.base.top + 6, opacity: 0, scale: 0.9 }}
          animate={{ left: g.to.left - g.base.left + 4 + (i % 3) * 18, top: g.to.top - g.base.top, opacity: [0, 1, 1, 0], scale: 1 }}
          transition={{ duration: 0.8, ease: EASE, delay: i * 0.05 }}>
          {g.label}
        </motion.span>
      ))}
    </div>
  );
}

function Lane({ agent, s, pulse, parallelTag }: { agent: CrewAgentKey; s: LaneState; pulse: Set<string>; parallelTag: boolean }) {
  const m = META[agent];
  return (
    <div data-testid={`crew-lane-${agent}`} data-status={s.status} className={clsx("grid gap-3 px-5 py-4 transition-colors md:grid-cols-[168px_minmax(0,1fr)]", s.status === "running" && "bg-paper/70")}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <LaneDot status={s.status} />
          <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">{m.title}</span>
          {parallelTag && <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded px-1 text-[10.5px] font-medium text-ink-2 ring-1 ring-inset ring-line">parallel</motion.span>}
        </div>
        <p className="mt-1 text-[12px] leading-snug text-ink-3">{m.role}</p>
        <p className="mt-1.5 font-mono text-[11.5px] text-ink-3 tabular">
          {s.status === "waiting" ? "waiting" : s.status === "running" ? "running" : `${s.status === "done" ? "done" : s.status.replace("_", " ")} · ${s.ms?.toFixed(1)} ms`}
        </p>
      </div>
      <div className="min-w-0">
        <div className="mb-2.5 h-1 overflow-hidden rounded-full bg-subtle">
          <motion.div className={clsx("h-full rounded-full", s.status === "error" ? "bg-signal" : "bg-ink")}
            initial={{ width: "0%" }} animate={{ width: s.status === "waiting" ? "0%" : s.status === "running" ? ["20%", "70%"] : "100%" }}
            transition={s.status === "running" ? { duration: 1.6, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" } : { duration: 0.35, ease: EASE }} />
        </div>
        <div data-reads={agent} className="min-h-[2px]">
          {s.reads.length > 0 && (
            <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.6 }} className="mb-2 flex flex-wrap items-center gap-1 text-[11.5px] text-ink-3">
              <span className="mr-0.5">Read from the graph</span>
              {s.reads.map((r) => <span key={r.id} title={r.id} className="rounded bg-subtle px-1 font-mono text-[11px] text-ink-2 ring-1 ring-inset ring-line">{r.agent}.{r.kind}</span>)}
            </motion.p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <AnimatePresence initial={false}>
            {s.findings.map((f) => (
              <motion.div key={f.id} data-fid={f.id} data-testid="crew-finding" layout
                initial={{ opacity: 0, y: 6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: 0.3, ease: EASE }}
                className={clsx("w-full rounded-lg border bg-surface px-3 py-2 transition-shadow sm:w-[calc(50%-4px)] xl:w-[calc(33.33%-6px)]",
                  pulse.has(f.id) ? "border-ink shadow-[0_0_0_3px_rgba(12,10,9,0.08)]" : "border-line")}>
                <p className="flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-wide text-ink-3">{f.kind.replace("_", " ")}</p>
                <p className="mt-0.5 text-[13px] font-medium leading-snug text-ink">{f.title}</p>
                <p className="mt-0.5 line-clamp-3 text-[12px] leading-snug text-ink-2">{f.text}</p>
              </motion.div>
            ))}
          </AnimatePresence>
          {s.status === "waiting" && <p className="text-[12px] text-ink-4">Waits for {agent === "fixer" ? "Investigator and Responder" : agent === "triage" ? "the trigger" : "Triage"}.</p>}
        </div>
      </div>
    </div>
  );
}

function LaneDot({ status }: { status: Status }) {
  if (status === "done") return <span className="grid h-4 w-4 place-items-center rounded-full bg-ink"><Check className="h-2.5 w-2.5 text-paper" strokeWidth={3} /></span>;
  if (status === "running") return <span className="relative grid h-4 w-4 place-items-center"><span className="absolute h-4 w-4 animate-ping rounded-full bg-ink/20" /><span className="h-2 w-2 rounded-full bg-ink" /></span>;
  if (status === "error") return <span className="h-4 w-4 rounded-full bg-signal" />;
  return <span className="h-4 w-4 rounded-full border-[1.5px] border-dashed border-line-strong" />;
}

// ------------------------------------------------------------------ handoff DAG

const POS: Record<CrewAgentKey, [number, number]> = { triage: [44, 90], investigator: [160, 38], responder: [160, 142], fixer: [276, 90] };
const curve = (a: CrewAgentKey, b: CrewAgentKey) => {
  const [x1, y1] = POS[a], [x2, y2] = POS[b];
  const mx = (x1 + x2) / 2;
  return `M ${x1 + 15} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 15} ${y2}`;
};
function bez(a: CrewAgentKey, b: CrewAgentKey, t: number): [number, number] {
  const [x1, y1] = POS[a], [x2, y2] = POS[b];
  const p0 = [x1 + 15, y1], p1 = [(x1 + x2) / 2, y1], p2 = [(x1 + x2) / 2, y2], p3 = [x2 - 15, y2];
  const u = 1 - t;
  return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]) as [number, number];
}

function Dag({ v }: { v: View }) {
  const drawn = new Map(v.handoffs.map((h) => [`${h.from}>${h.to}`, h]));
  return (
    <div className="border-b border-line px-4 py-3">
      <p className="mb-1 text-[12px] font-medium text-ink-2">Handoffs</p>
      <svg viewBox="0 0 320 180" className="h-auto w-full" data-testid="crew-dag" role="img" aria-label="Triage hands off to Investigator and Responder in parallel; both hand off to Fixer">
        {DAG.map(([a, b]) => {
          const h = drawn.get(`${a}>${b}`);
          const pts = Array.from({ length: 12 }, (_, i) => bez(a, b, i / 11));
          return (
            <g key={`${a}-${b}`}>
              <path d={curve(a, b)} fill="none" stroke="#D6D3D1" strokeWidth={1.2} strokeDasharray="3 4" />
              {h && (
                <>
                  <motion.path data-testid="crew-edge" data-from={a} data-to={b} d={curve(a, b)} fill="none" stroke="#0C0A09" strokeWidth={1.6}
                    initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.5, ease: EASE }}>
                    <title>{h.reason}</title>
                  </motion.path>
                  <motion.circle r={3} fill="#0C0A09" initial={{ cx: pts[0][0], cy: pts[0][1], opacity: 1 }}
                    animate={{ cx: pts.map((p) => p[0]), cy: pts.map((p) => p[1]), opacity: [1, 1, 0] }} transition={{ duration: 0.55, ease: "easeInOut" }} />
                </>
              )}
            </g>
          );
        })}
        {AGENTS.map((a) => {
          const s = v.lanes[a].status;
          const [x, y] = POS[a];
          return (
            <g key={a} data-node={a} data-status={s}>
              {s === "running" && <motion.circle cx={x} cy={y} r={14} fill="none" stroke="#0C0A09" strokeOpacity={0.25} initial={{ r: 14, opacity: 1 }} animate={{ r: 22, opacity: 0 }} transition={{ duration: 1.2, repeat: Infinity }} />}
              <circle cx={x} cy={y} r={14} fill={s === "done" ? "#0C0A09" : "#FFFFFF"} stroke={s === "waiting" ? "#D6D3D1" : s === "error" ? "#E5484D" : "#0C0A09"} strokeWidth={1.5} />
              {s === "done" && <path d={`M ${x - 5} ${y} l 3.5 3.5 l 6.5 -7`} fill="none" stroke="#FAFAF9" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />}
              {s === "running" && <circle cx={x} cy={y} r={4} fill="#0C0A09" />}
              <text x={x} y={y + (a === "investigator" ? -22 : 30)} textAnchor="middle" fontSize={11.5} className={s === "waiting" ? "fill-ink-3" : "fill-ink"} fontWeight={500}>{META[a].title}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Each agent's active window in event order: Investigator and Responder overlap because their events interleave. */
function Timeline({ v, total }: { v: View; total: number }) {
  return (
    <div className="border-b border-line px-4 py-3" data-testid="crew-timeline">
      <p className="mb-2 flex items-center justify-between text-[12px] font-medium text-ink-2">Activity<span className="font-normal text-ink-3">by event order</span></p>
      <div className="space-y-1.5">
        {AGENTS.map((a) => {
          const s = v.lanes[a];
          const start = s.startIdx ?? total, end = s.endIdx ?? (s.status === "running" ? total : start);
          return (
            <div key={a} className="grid grid-cols-[78px_minmax(0,1fr)_52px] items-center gap-2 text-[11.5px]">
              <span className="text-ink-2">{META[a].title}</span>
              <div className="relative h-2 rounded-full bg-subtle">
                {s.startIdx !== undefined && (
                  <motion.div className="absolute inset-y-0 rounded-full bg-ink" initial={false}
                    animate={{ left: `${(start / total) * 100}%`, width: `${Math.max(((end - start + 1) / total) * 100, 2)}%` }} transition={{ duration: 0.3, ease: EASE }} />
                )}
              </div>
              <span className="text-right font-mono text-ink-3 tabular">{s.ms !== undefined ? `${s.ms.toFixed(1)}` : ""}<span className="text-ink-4">{s.ms !== undefined ? " ms" : ""}</span></span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EventLog({ v }: { v: View }) {
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }); }, [v.log.length]);
  return (
    <div className="px-4 py-3">
      <p className="mb-1.5 text-[12px] font-medium text-ink-2">Event stream</p>
      <ol ref={ref} className="scroll-thin max-h-[188px] space-y-0.5 overflow-y-auto font-mono text-[11px] leading-relaxed">
        {v.log.map((l) => (
          <li key={l.idx} className="flex gap-2"><span className="w-5 shrink-0 text-right text-ink-4 tabular">{l.idx + 1}</span><span className="w-[74px] shrink-0 text-ink-2">{l.who}</span><span className="min-w-0 text-ink-3">{l.text}</span></li>
        ))}
      </ol>
    </div>
  );
}

// ------------------------------------------------------------------ end state

function find(r: CrewRun, kind: string): CrewFinding | undefined {
  for (const a of r.agents) for (const f of a.findings ?? []) if (f.kind === kind) return f;
  return undefined;
}

function Summary({ r }: { r: CrewRun }) {
  const br = find(r, "blast_radius")?.data ?? {};
  const wake = find(r, "wake")?.data?.teams ?? [];
  const drafts = find(r, "page_drafts")?.data?.drafts ?? [];
  const plan = find(r, "patch_plan")?.data ?? {};
  const pb = find(r, "playbook");
  const delta = (find(r, "memory_delta")?.data ?? r.incident?.delta ?? {}) as Record<string, unknown>;
  const exposed: { service: string; tier: number; team: string; exposed_packages?: string[] }[] = br.exposed ?? [];
  const fleet = br.fleet_size ?? 0;
  return (
    <motion.div data-testid="crew-summary" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }} className="border-t border-line bg-paper/50 px-5 py-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[12px] font-medium text-ink-3">Crew summary</p>
          <h3 className="mt-1 text-[24px] font-semibold leading-tight tracking-[-0.02em] text-ink">
            <span className={exposed.length ? "text-signal" : "text-safe"}>{exposed.length}</span> of {fleet} {r.company} services exposed
          </h3>
          <p className="mt-1 max-w-[760px] text-[13.5px] text-ink-2">{r.message}</p>
        </div>
        {r.incident && <p className="font-mono text-[12px] text-ink-3">{r.incident.id}</p>}
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Tile title="Exposed" sub={`${br.safe?.length ?? 0} more reach an affected package, ranges exclude it`}>
          <ul className="space-y-1.5">
            {exposed.map((e) => (
              <li key={e.service} className="flex items-center justify-between gap-2 text-[13px]">
                <span className="flex min-w-0 items-center gap-2"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-signal" /><span className="truncate font-mono text-ink">{e.service}</span></span>
                <span className="shrink-0 text-[12px] text-ink-3">tier {e.tier} · {e.team}</span>
              </li>
            ))}
          </ul>
        </Tile>
        <Tile title="Who to page" sub={`${wake.length} team${wake.length === 1 ? "" : "s"}, highest tier first`}>
          <ul className="space-y-2" data-testid="crew-wake">
            {wake.map((t: { team: string; team_display: string; slack: string; oncall: { name: string; handle: string }[]; services: string[]; top_tier: number }) => (
              <li key={t.team} className="text-[13px]">
                <p className="flex items-center justify-between gap-2"><span className="font-medium text-ink">{t.oncall.map((o) => o.name).join(", ") || t.team_display}</span><span className="font-mono text-[12px] text-ink-3">{t.slack}</span></p>
                <p className="truncate text-[12px] text-ink-3">{t.team_display} · {t.services.join(", ")}</p>
              </li>
            ))}
          </ul>
        </Tile>
        <Tile title="Page drafts" sub="Drafts only. Nothing is sent." right={<span className="inline-flex items-center gap-1 rounded-md bg-subtle px-1.5 py-0.5 text-[11px] font-medium text-ink-2 ring-1 ring-inset ring-line"><Send className="h-3 w-3" />Not sent</span>}>
          <Drafts drafts={drafts} />
        </Tile>
        <Tile title="Patch plan" sub={`${(plan.order ?? []).length} services, tier 1 first`} className="md:col-span-2 xl:col-span-1">
          <ol className="mb-3 space-y-1">
            {(plan.order ?? []).map((s: string, i: number) => (
              <li key={s} className="flex items-center gap-2 text-[13px]"><span className="w-4 text-right font-mono text-[11.5px] text-ink-3">{i + 1}</span><span className="font-mono text-ink">{s}</span></li>
            ))}
          </ol>
          {plan.combined && (
            <div>
              <div className="mb-1 flex items-center justify-between"><span className="text-[12px] text-ink-3">Fleet-wide overrides</span><CopyButton text={JSON.stringify(plan.combined, null, 2)} /></div>
              <pre className="scroll-thin max-h-40 overflow-auto rounded-lg border border-line bg-subtle p-2.5 font-mono text-[11.5px] leading-relaxed text-ink">{JSON.stringify(plan.combined, null, 2)}</pre>
            </div>
          )}
          {(plan.exact_pins ?? []).length > 0 && <p className="mt-2 text-[12px] leading-snug text-ink-3">Pinned exactly to today&apos;s published version (no fixed release listed): {plan.exact_pins.join(", ")}.</p>}
        </Tile>
        <Tile title="Playbook reuse" sub="Approved remediations, remembered in the graph">
          {pb?.data?.playbook_id ? (
            <div className="text-[13px]">
              <p className="font-mono text-ink">{pb.data.playbook_id}</p>
              <p className="mt-1 text-ink-2"><span className="text-[22px] font-semibold text-ink tabular">{pb.data.uses_count ?? 0}</span> reuses{pb.data.approved_by ? `, approved by ${pb.data.approved_by}` : ""}</p>
              <p className="mt-1 text-[12px] text-ink-3">{pb.data.agrees ? "Its pins agree with this plan." : "Its pins differ from this plan: review before applying."}</p>
            </div>
          ) : <p className="text-[13px] leading-relaxed text-ink-2">{pb?.text ?? "No approved playbook covers these packages yet."}</p>}
        </Tile>
        <Tile title="Memory delta" sub="What changed since the last triage of this trigger">
          <MemoryDelta d={delta} />
        </Tile>
      </div>
    </motion.div>
  );
}

function Tile({ title, sub, right, className, children }: { title: string; sub?: string; right?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <div className={clsx("rounded-xl border border-line bg-surface p-4", className)}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <div><p className="text-[13.5px] font-semibold tracking-[-0.01em] text-ink">{title}</p>{sub && <p className="mt-0.5 text-[12px] text-ink-3">{sub}</p>}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Drafts({ drafts }: { drafts: { team: string; channel: string; severity: string; text: string }[] }) {
  const [open, setOpen] = useState(0);
  return (
    <div className="space-y-1.5" data-testid="crew-drafts">
      {drafts.map((d, i) => (
        <div key={d.team} className="rounded-lg border border-line">
          <button type="button" onClick={() => setOpen(open === i ? -1 : i)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px]">
            <span className={clsx("rounded px-1 font-mono text-[10.5px] font-semibold", d.severity === "SEV1" ? "bg-signal-soft text-signal-ink" : "bg-subtle text-ink-2")}>{d.severity}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-ink">{d.channel}</span>
            <span className="text-[11px] text-ink-3">Draft</span>
            <ChevronDown className={clsx("h-3.5 w-3.5 text-ink-3 transition-transform", open === i && "rotate-180")} />
          </button>
          {open === i && <p className="border-t border-line px-2.5 py-2 text-[12px] leading-relaxed text-ink-2">{d.text}</p>}
        </div>
      ))}
    </div>
  );
}

function MemoryDelta({ d }: { d: Record<string, unknown> }) {
  const list = (k: string) => (Array.isArray(d[k]) ? (d[k] as string[]) : []);
  const n = Number(d.triage_count ?? 1);
  if (d.first_triage) {
    return <p className="text-[13px] leading-relaxed text-ink-2">First triage of this trigger. The incident is now in graph memory; the next run will report what changed.</p>;
  }
  const rows: [string, string[], string][] = [["Newly exposed", list("newly_exposed"), "text-signal-ink"], ["No longer exposed", list("no_longer_exposed"), "text-safe"], ["Unchanged", list("unchanged"), "text-ink-2"]];
  return (
    <div className="space-y-2 text-[13px]">
      <p className="text-ink-2">Triage <span className="font-semibold text-ink tabular">{n}</span>{d.previous_seen ? <>, last seen {new Date(String(d.previous_seen)).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</> : null}</p>
      {rows.map(([label, xs, cls]) => (
        <p key={label} className="flex gap-2"><span className="w-[118px] shrink-0 text-ink-3">{label}</span><span className={clsx("min-w-0 font-mono text-[12px]", xs.length ? cls : "text-ink-4")}>{xs.length ? xs.join(", ") : "none"}</span></p>
      ))}
    </div>
  );
}
