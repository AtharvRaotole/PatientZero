"use client";
/* Insight views. Every mark is derived from graph results (paths, findings, ranks, dated events). */
import { motion } from "framer-motion";
import clsx from "clsx";
import { BookCheck, Brain, CalendarDays, FileWarning, Skull } from "lucide-react";
import { useMemo, useState } from "react";
import type { AskResponse, ServiceExposure, Spof } from "@/lib/types";


/* ------------------------------------------------------------------ Blast radius rings */
export function BlastRings({ r }: { r: AskResponse }) {
  const [hover, setHover] = useState<string | null>(null);
  const model = useMemo(() => buildRings([...r.exposed, ...r.safe]), [r.exposed, r.safe]);
  const S = 560, C = S / 2, R = S / 2 - 46;
  if (!model.nodes.length) return <Empty text="No dependency chains reach an affected package at this depth." />;
  const ringR = (d: number) => (d === 0 ? 0 : 34 + ((R - 34) * d) / model.maxD);
  const pos = (id: string) => {
    const n = model.byId.get(id)!;
    const rad = n.d === 0 ? 22 : ringR(n.d);
    return [C + rad * Math.cos(n.angle), C + rad * Math.sin(n.angle)];
  };
  const active = hover ? model.chainOf.get(hover) : null;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
      <svg viewBox={`0 0 ${S} ${S}`} className="mx-auto w-full max-w-[560px]">
        <defs>
          <radialGradient id="core" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ff4d6d" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#ff4d6d" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={C} cy={C} r={70} fill="url(#core)" />
        {Array.from({ length: model.maxD }, (_, i) => i + 1).map((d) => (
          <g key={d}>
            <circle cx={C} cy={C} r={ringR(d)} fill="none" stroke="rgba(255,255,255,0.07)" strokeDasharray="2 5" />
            <rect x={C - ringR(d) * 0.7071 - 20} y={C + ringR(d) * 0.7071 - 7} width={40} height={14} rx={7} fill="#0b0d14" opacity={0.85} />
            <text x={C - ringR(d) * 0.7071} y={C + ringR(d) * 0.7071 + 0.5} fill="rgba(255,255,255,0.4)" fontSize="9.5" textAnchor="middle" dominantBaseline="middle">{d} hop{d > 1 ? "s" : ""}</text>
          </g>
        ))}
        {model.edges.map((e) => {
          const [x1, y1] = pos(e.a), [x2, y2] = pos(e.b);
          const on = !active || (active.has(e.a) && active.has(e.b));
          return (
            <path key={e.a + e.b} d={`M${x1},${y1} Q${(x1 + x2) / 2 + (C - (x1 + x2) / 2) * 0.15},${(y1 + y2) / 2 + (C - (y1 + y2) / 2) * 0.15} ${x2},${y2}`}
              fill="none" stroke={e.exposed ? "#ff4d6d" : "#34d399"} strokeOpacity={on ? (e.exposed ? 0.75 : 0.45) : 0.06} strokeWidth={e.exposed ? 1.6 : 1}
              strokeDasharray={e.exposed ? "4 4" : undefined} className={e.exposed ? "animate-[dash_1.2s_linear_infinite]" : undefined} />
          );
        })}
        {model.nodes.map((n) => {
          const [x, y] = pos(n.id);
          const on = !active || active.has(n.id);
          const col = n.d === 0 ? "#ff4d6d" : n.service ? (n.exposed ? "#ff8fa3" : "#6ee7b7") : n.exposed ? "#fbbf24" : "rgba(148,163,184,0.8)";
          const size = n.d === 0 ? 7 : n.service ? 5.5 : 3;
          const outer = n.service;
          const lx = outer ? C + (ringR(n.d) + 10) * Math.cos(n.angle) : x;
          const ly = outer ? C + (ringR(n.d) + 10) * Math.sin(n.angle) : y - 8;
          return (
            <g key={n.id} opacity={on ? 1 : 0.15} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} className="cursor-pointer">
              <circle cx={x} cy={y} r={size + 6} fill={col} opacity={0.12} />
              <circle cx={x} cy={y} r={size} fill={col} style={{ filter: `drop-shadow(0 0 6px ${col})` }} />
              {(n.service || n.d === 0 || hover === n.id) && (
                <text x={lx} y={ly} fill={n.d === 0 ? "#ffd1da" : "rgba(226,232,255,0.85)"} fontSize={n.d === 0 ? 11 : 10} fontWeight={n.service || n.d === 0 ? 600 : 400}
                  textAnchor={outer ? (Math.cos(n.angle) > 0.2 ? "start" : Math.cos(n.angle) < -0.2 ? "end" : "middle") : "middle"} dominantBaseline="middle">
                  {n.d === 0 ? n.label : n.id}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="space-y-3 text-[12px] text-white/55">
        <p className="text-white/75">Rings count hops outward from the compromised package (center) to the services that pull it.</p>
        <p><span className="text-rose-300">Red chains</span> end in a range that admits the malicious version. <span className="text-emerald-300">Green</span> chains reach the package but their range excludes it.</p>
        <div className="space-y-1">
          {Array.from({ length: model.maxD }, (_, i) => i + 1).map((d) => {
            const svcs = model.nodes.filter((n) => n.service && n.d === d);
            return svcs.length ? (
              <div key={d} className="flex items-center justify-between rounded-lg bg-white/[0.03] px-2.5 py-1">
                <span>{d} hop{d > 1 ? "s" : ""}</span>
                <span><b className="text-rose-300">{svcs.filter((s) => s.exposed).length}</b> exposed · <b className="text-emerald-300">{svcs.filter((s) => !s.exposed).length}</b> safe</span>
              </div>
            ) : null;
          })}
        </div>
        <p className="text-[11px] text-white/35">Hover a node to isolate its chain. One representative path per service and package.</p>
      </div>
    </div>
  );
}

type RNode = { id: string; label: string; d: number; angle: number; service: boolean; exposed: boolean };

function buildRings(rows: ServiceExposure[]) {
  const nodes = new Map<string, RNode>();
  const edges = new Map<string, { a: string; b: string; exposed: boolean }>();
  const chainOf = new Map<string, Set<string>>();
  const services: { id: string; exposed: boolean; key: string }[] = [];
  let maxD = 1;
  const chains: { via: string[]; exposed: boolean; svc: string; center: string }[] = [];
  for (const e of rows) {
    for (const f of e.findings) {
      const via = f.path.via;
      chains.push({ via, exposed: f.path.exposed, svc: e.service, center: `pkg:${f.package}` });
    }
    services.push({ id: e.service, exposed: e.exposed_packages.length > 0, key: e.findings[0]?.package ?? "" });
  }
  services.sort((a, b) => a.key.localeCompare(b.key) || Number(b.exposed) - Number(a.exposed) || a.id.localeCompare(b.id));
  const svcAngle = new Map(services.map((s, i) => [s.id, (i / Math.max(services.length, 1)) * 2 * Math.PI - Math.PI / 2]));
  const angleSum = new Map<string, { s: number; c: number; n: number }>();
  const addAngle = (id: string, a: number) => {
    const v = angleSum.get(id) ?? { s: 0, c: 0, n: 0 };
    v.s += Math.sin(a); v.c += Math.cos(a); v.n += 1;
    angleSum.set(id, v);
  };
  for (const ch of chains) {
    const rev = [...ch.via].reverse(); // [resolved affected version, ..., service]
    const ids = rev.map((v, i) => (i === 0 ? ch.center : v));
    const a = svcAngle.get(ch.svc) ?? 0;
    ids.forEach((id, d) => {
      const isSvc = d === ids.length - 1;
      const prev = nodes.get(id);
      const depth = d;
      maxD = Math.max(maxD, depth);
      if (!prev || depth < prev.d) nodes.set(id, { id, label: d === 0 ? ch.center.slice(4) : id, d: depth, angle: 0, service: isSvc, exposed: (prev?.exposed ?? false) || ch.exposed });
      else prev.exposed = prev.exposed || ch.exposed;
      addAngle(id, a);
    });
    for (let i = 0; i < ids.length - 1; i++) {
      const k = ids[i] + "|" + ids[i + 1];
      const old = edges.get(k);
      edges.set(k, { a: ids[i], b: ids[i + 1], exposed: (old?.exposed ?? false) || ch.exposed });
    }
    const set = new Set(ids);
    ids.forEach((id) => chainOf.set(id, new Set([...(chainOf.get(id) ?? []), ...set])));
  }
  for (const n of nodes.values()) {
    if (n.service && svcAngle.has(n.id)) n.angle = svcAngle.get(n.id)!;
    else {
      const v = angleSum.get(n.id)!;
      n.angle = Math.atan2(v.s, v.c);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()], byId: nodes, chainOf, maxD };
}

/* ------------------------------------------------------------------ Exposure matrix */
export function ExposureMatrix({ r }: { r: AskResponse }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string; sub: string } | null>(null);
  const { rows, cols, cell } = useMemo(() => {
    const cell = new Map<string, { exposed: boolean; n: number; display: string; reason: string }>();
    const colScore = new Map<string, number>();
    for (const e of [...r.exposed, ...r.safe]) {
      for (const f of e.findings) {
        cell.set(`${e.service}|${f.package}`, { exposed: f.exposed, n: f.exposed ? f.exposed_path_count ?? f.path_count : f.path_count, display: f.path.display, reason: f.path.reason });
        colScore.set(f.package, (colScore.get(f.package) ?? 0) + (f.exposed ? 10 : 1));
      }
    }
    const cols = [...colScore.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    const score = (s: string) => cols.reduce((a, c) => a + (cell.get(`${s}|${c}`)?.exposed ? 10 : cell.has(`${s}|${c}`) ? 1 : 0), 0);
    const all = [...r.exposed.map((e) => e.service), ...r.safe.map((e) => e.service), ...r.clean];
    const rows = all.sort((a, b) => score(b) - score(a) || a.localeCompare(b));
    return { rows, cols, cell };
  }, [r]);
  if (!cols.length) return <Empty text="No service reaches an affected package at this depth." />;
  const maxN = Math.max(...[...cell.values()].map((c) => c.n), 1);
  return (
    <div className="relative">
      <div className="scroll-thin flex justify-center overflow-x-auto">
        <table className="border-separate border-spacing-[3px] text-[11px]">
          <thead>
            <tr>
              <th />
              {cols.map((c) => (
                <th key={c} className={clsx("align-bottom font-mono font-normal text-white/55", cols.length > 8 && "h-24")}>{cols.length <= 8 ? <div className="pb-1 text-center">{c}</div> : <div className="w-6 origin-bottom-left translate-x-3 -rotate-45 whitespace-nowrap">{c}</div>}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s}>
                <td className="whitespace-nowrap pr-2 text-right text-white/70">{s}</td>
                {cols.map((c) => {
                  const v = cell.get(`${s}|${c}`);
                  const op = v ? 0.25 + 0.75 * Math.sqrt(v.n / maxN) : 0;
                  return (
                    <td key={c} className="p-0">
                      <motion.div initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.002 * (rows.indexOf(s) * cols.length + cols.indexOf(c)) }}
                        onMouseMove={(ev) => v && setTip({ x: ev.clientX, y: ev.clientY, text: v.display, sub: `${v.reason} · ${v.n} path${v.n > 1 ? "s" : ""}` })}
                        onMouseLeave={() => setTip(null)}
                        className={clsx("h-6 rounded-[5px]", cols.length <= 8 ? "w-14" : "w-6", !v && "bg-white/[0.03]", v && !v.exposed && "ring-1 ring-inset ring-emerald-400/50")}
                        style={v ? { background: v.exposed ? `rgba(255,77,109,${op})` : "rgba(52,211,153,0.10)", boxShadow: v.exposed ? `0 0 ${10 * op}px rgba(255,77,109,${op * 0.7})` : undefined } : undefined} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-white/45">
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-rose-500/80" />exposed (shade = number of admitting paths)</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] ring-1 ring-inset ring-emerald-400/60" />reaches the package, range excludes it</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-white/[0.05]" />no path at this depth</span>
      </div>
      {tip && (
        <div className="pointer-events-none fixed z-50 max-w-md rounded-xl border border-white/10 bg-[#0c0e16]/95 px-3 py-2 text-[11px] shadow-2xl" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <p className="font-mono text-white/85">{tip.text}</p>
          <p className="mt-0.5 text-white/45">{tip.sub}</p>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ SPOF scatter: reach x PageRank */
export function SpofScatter({ spof }: { spof: Spof }) {
  const [hover, setHover] = useState<string | null>(null);
  const pts = spof.maintainers;
  if (!pts.length) return null;
  const W = 520, H = 300, P = 40;
  const maxR = Math.max(...pts.map((p) => p.reach)), minR = Math.min(...pts.map((p) => p.reach));
  const maxPr = Math.max(...pts.map((p) => p.pagerank), 1e-9);
  const x = (v: number) => P + ((v - minR + 1) / (maxR - minR + 2)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / maxPr) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <line x1={P} y1={H - P} x2={W - P / 2} y2={H - P} stroke="rgba(255,255,255,0.12)" />
      <line x1={P} y1={P / 2} x2={P} y2={H - P} stroke="rgba(255,255,255,0.12)" />
      <text x={W - P / 2} y={H - 10} textAnchor="end" fill="rgba(255,255,255,0.4)" fontSize="10">fleet reach (services) →</text>
      <text x={10} y={P / 2} fill="rgba(255,255,255,0.4)" fontSize="10">PageRank ↑</text>
      {[minR, maxR].filter((v, i, a) => a.indexOf(v) === i).map((v) => (
        <text key={v} x={x(v)} y={H - P + 13} textAnchor="middle" fill="rgba(255,255,255,0.35)" fontSize="9.5">{v}</text>
      ))}
      <text x={P - 5} y={y(maxPr) + 3} textAnchor="end" fill="rgba(255,255,255,0.35)" fontSize="9.5">{maxPr.toFixed(2)}</text>
      <text x={P - 5} y={H - P + 3} textAnchor="end" fill="rgba(255,255,255,0.35)" fontSize="9.5">0</text>
      <text x={P + 6} y={H - 10} fill="rgba(255,255,255,0.3)" fontSize="9.5">halo = packages maintained in fleet</text>
      {pts.map((p, i) => {
        const cx = x(p.reach), cy = y(p.pagerank);
        const col = p.compromised_in_incident ? "#ff4d6d" : "#7c9cff";
        return (
          <motion.g key={p.maintainer} initial={{ opacity: 0, scale: 0 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: i * 0.05 }} onMouseEnter={() => setHover(p.maintainer)} onMouseLeave={() => setHover(null)}>
            <circle cx={cx} cy={cy} r={6 + p.packages_in_fleet * 0.4} fill={col} opacity={0.18} />
            <circle cx={cx} cy={cy} r={4} fill={col} style={{ filter: `drop-shadow(0 0 6px ${col})` }} />
            {(i < 5 || hover === p.maintainer) && <text x={cx + 8} y={cy - 6} fill="rgba(226,232,255,0.85)" fontSize="10.5">{p.maintainer}</text>}
            {hover === p.maintainer && <text x={cx + 8} y={cy + 8} fill="rgba(255,255,255,0.5)" fontSize="9.5">{p.reach} services · pr {p.pagerank.toFixed(2)} · {p.packages_in_fleet} pkgs</text>}
          </motion.g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ Incident timeline */
export type TimelineEvent = { kind: "compromise" | "advisory" | "incident" | "playbook"; at: string; title: string; count: number; items: string[]; ref: string | null };

const KIND = {
  compromise: { icon: Skull, color: "text-rose-300", ring: "ring-rose-400/40 bg-rose-500/15", label: "Account compromised" },
  advisory: { icon: FileWarning, color: "text-amber-200", ring: "ring-amber-400/40 bg-amber-500/15", label: "Advisories published" },
  incident: { icon: Brain, color: "text-violet-200", ring: "ring-violet-400/40 bg-violet-500/15", label: "Incident written to graph" },
  playbook: { icon: BookCheck, color: "text-emerald-200", ring: "ring-emerald-400/40 bg-emerald-500/15", label: "Playbook approved" },
} as const;

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const groups = useMemo(() => {
    const out: { day: string; kind: TimelineEvent["kind"]; items: TimelineEvent[] }[] = [];
    for (const e of events) {
      const day = String(e.at).slice(0, 10);
      const last = out[out.length - 1];
      if (last && last.day === day && last.kind === e.kind) last.items.push(e);
      else out.push({ day, kind: e.kind, items: [e] });
    }
    return out;
  }, [events]);
  if (!events.length) return <Empty text="No dated events in the graph yet." />;
  return (
    <ol className="relative ml-3 border-l border-white/10">
      {groups.map((g, i) => {
        const k = KIND[g.kind];
        const Icon = k.icon;
        const first = g.items[0];
        const fmt = (s: string) => (s.length > 10 ? new Date(s).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : new Date(s + "T12:00:00Z").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }));
        return (
          <motion.li key={i} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06 }} className="mb-5 ml-6">
            <span className={clsx("absolute -left-[13px] grid h-6 w-6 place-items-center rounded-full ring-1", k.ring)}><Icon className={clsx("h-3.5 w-3.5", k.color)} /></span>
            <div className="flex flex-wrap items-baseline gap-2">
              <p className={clsx("text-[13px] font-semibold", k.color)}>{g.kind === "advisory" ? `${g.items.length} advisor${g.items.length > 1 ? "ies" : "y"} published` : k.label}</p>
              <span className="text-[11px] text-white/40"><CalendarDays className="mr-1 inline h-3 w-3" />{fmt(first.at)}{g.items.length > 1 ? ` to ${fmt(g.items[g.items.length - 1].at).split(",").slice(-1)[0]}` : ""}</span>
            </div>
            {g.kind === "compromise" && <p className="mt-1 text-[12.5px] text-white/65">npm account <span className="font-mono text-white/85">{first.title}</span> had publish rights on {first.count} packages that shipped malicious versions.</p>}
            {g.kind === "incident" && g.items.map((e) => <p key={e.title} className="mt-1 text-[12.5px] text-white/65"><span className="font-mono text-violet-200/90">{e.title}</span>: {e.count} exposed services, trigger {e.items[0]}</p>)}
            {g.kind === "playbook" && g.items.map((e) => <p key={e.title + e.at} className="mt-1 text-[12.5px] text-white/65">{e.title}</p>)}
            {(g.kind === "advisory" || g.kind === "compromise") && (
              <div className="mt-2 flex flex-wrap gap-1">
                {(g.kind === "advisory" ? g.items.map((e) => e.items[0]) : first.items).map((t) => (
                  <span key={t} className="rounded-md bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10.5px] text-white/60">{t}</span>
                ))}
              </div>
            )}
          </motion.li>
        );
      })}
    </ol>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="grid place-items-center rounded-xl border border-dashed border-white/10 py-12 text-sm text-white/40">{text}</div>;
}
