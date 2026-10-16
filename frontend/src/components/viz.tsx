"use client";
/* Insight views. Every mark is derived from graph results (paths, findings, ranks, dated events). */
import { motion } from "framer-motion";
import clsx from "clsx";
import { BookCheck, Brain, CalendarDays, FileWarning, Skull } from "lucide-react";
import { useMemo, useState } from "react";
import type { AskResponse, ServiceExposure, Spof } from "@/lib/types";

/* Light paper palette. One signal color; amber marks the exposure chain. */
const INK = "#0C0A09", INK2 = "#57534E", STONE = "#A8A29E", LINE = "#E7E5E4", PAPER = "#FAFAF9", SIG = "#E5484D", RISK = "#B45309";


/* ------------------------------------------------------------------ Blast radius rings */
export function BlastRings({ r }: { r: AskResponse }) {
  const [hover, setHover] = useState<string | null>(null);
  const [coreOpen, setCoreOpen] = useState(false);
  const model = useMemo(() => buildRings([...r.exposed, ...r.safe]), [r.exposed, r.safe]);
  const core = useMemo(() => model.nodes.filter((n) => n.d === 0).sort((a, b) => a.angle - b.angle), [model]);
  const S = 560, C = S / 2, R = S / 2 - 46;
  if (!model.nodes.length) return <Empty text="No dependency chains reach an affected package at this depth." />;
  const ringR = (d: number) => (d === 0 ? 0 : 58 + ((R - 58) * d) / model.maxD);
  // The compromised packages collapse into one core node; hovering it fans them out on a small inner circle.
  const coreR = Math.min(ringR(1) - 22, Math.max(26, (core.length * 15) / (2 * Math.PI)));
  const coreSlot = new Map(core.map((n, i) => [n.id, -Math.PI / 2 + (i / core.length) * 2 * Math.PI]));
  const pos = (id: string) => {
    const n = model.byId.get(id)!;
    if (n.d === 0) {
      if (!coreOpen || core.length === 1) return [C, C];
      const a = coreSlot.get(id)!;
      return [C + coreR * Math.cos(a), C + coreR * Math.sin(a)];
    }
    return [C + ringR(n.d) * Math.cos(n.angle), C + ringR(n.d) * Math.sin(n.angle)];
  };
  const active = hover ? model.chainOf.get(hover) : null;
  const spring = { type: "spring", stiffness: 260, damping: 30 } as const;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
      <svg viewBox={`0 0 ${S} ${S}`} className="mx-auto w-full max-w-[560px] overflow-visible">
        {Array.from({ length: model.maxD }, (_, i) => i + 1).map((d) => (
          <g key={d}>
            <circle cx={C} cy={C} r={ringR(d)} fill="none" stroke={LINE} strokeWidth={1} />
            <rect x={C - ringR(d) * 0.7071 - 20} y={C + ringR(d) * 0.7071 - 8} width={40} height={16} rx={4} fill="#FFFFFF" />
            <text x={C - ringR(d) * 0.7071} y={C + ringR(d) * 0.7071 + 0.5} fill={STONE} fontSize="10" textAnchor="middle" dominantBaseline="middle" style={{ fontVariantNumeric: "tabular-nums" }}>{d} hop{d > 1 ? "s" : ""}</text>
          </g>
        ))}
        {model.edges.map((e) => {
          const [x1, y1] = pos(e.a), [x2, y2] = pos(e.b);
          const on = !active || (active.has(e.a) && active.has(e.b));
          const d = `M${x1},${y1} Q${(x1 + x2) / 2 + (C - (x1 + x2) / 2) * 0.15},${(y1 + y2) / 2 + (C - (y1 + y2) / 2) * 0.15} ${x2},${y2}`;
          return (
            <motion.path key={e.a + e.b} initial={false} animate={{ d }} transition={spring}
              fill="none" stroke={e.exposed ? SIG : STONE} strokeOpacity={on ? (e.exposed ? 0.9 : 0.55) : 0.08} strokeWidth={e.exposed ? 1.4 : 1}
              style={{ transition: "stroke-opacity 180ms ease-out" }} />
          );
        })}
        {model.nodes.filter((n) => n.d > 0).map((n) => {
          const [x, y] = pos(n.id);
          const on = !active || active.has(n.id);
          const col = n.service ? (n.exposed ? SIG : INK) : n.exposed ? RISK : STONE;
          const hollow = n.service && !n.exposed;
          const size = n.service ? 5.5 : 3;
          const lx = n.service ? C + (ringR(n.d) + 10) * Math.cos(n.angle) : x;
          const ly = n.service ? C + (ringR(n.d) + 10) * Math.sin(n.angle) : y - 8;
          return (
            <g key={n.id} opacity={on ? 1 : 0.15} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} className="cursor-pointer" style={{ transition: "opacity 180ms ease-out" }}>
              <circle cx={x} cy={y} r={size + 8} fill="transparent" />
              <circle cx={x} cy={y} r={size} fill={hollow ? "#FFFFFF" : col} stroke={hollow ? INK : PAPER} strokeWidth={1.5} />
              {(n.service || hover === n.id) && (
                <text x={lx} y={ly} fill={INK2} fontSize={10.5} fontWeight={500} paintOrder="stroke" stroke="#FFFFFF" strokeWidth={3} strokeLinejoin="round"
                  textAnchor={n.service ? (Math.cos(n.angle) > 0.2 ? "start" : Math.cos(n.angle) < -0.2 ? "end" : "middle") : "middle"} dominantBaseline="middle">
                  {n.id}
                </text>
              )}
            </g>
          );
        })}
        {/* core: the compromised packages */}
        <g onMouseEnter={() => setCoreOpen(true)} onMouseLeave={() => { setCoreOpen(false); setHover(null); }} className="cursor-pointer">
          <circle cx={C} cy={C} r={coreOpen ? coreR + 14 : 22} fill="transparent" />
          <motion.circle cx={C} cy={C} initial={false} animate={{ r: coreOpen ? coreR + 9 : 15, opacity: coreOpen ? 0.5 : 0.35 }} transition={spring} fill="#FFFFFF" stroke={SIG} strokeWidth={1} />
          {core.map((n) => {
            const [x, y] = pos(n.id);
            const on = !active || active.has(n.id);
            return (
              <motion.g key={n.id} initial={false} animate={{ x: x - C, y: y - C, opacity: on ? 1 : 0.15 }} transition={spring}
                onMouseEnter={() => coreOpen && setHover(n.id)} onMouseLeave={() => setHover(null)}>
                <circle cx={C} cy={C} r={coreOpen ? 4 : 9} fill={SIG} stroke="#FFFFFF" strokeWidth={1.5} style={{ transition: "r 200ms ease-out" }} />
              </motion.g>
            );
          })}
          {!coreOpen && core.length > 1 && (
            <text x={C} y={C + 0.5} fill="#FFFFFF" fontSize="10" fontWeight={600} textAnchor="middle" dominantBaseline="middle" style={{ fontVariantNumeric: "tabular-nums", pointerEvents: "none" }}>{core.length}</text>
          )}
          {hover && model.byId.get(hover)?.d === 0 && (() => {
            const [x, y] = pos(hover);
            return <text x={x} y={y - 10} fill={INK} fontSize={11.5} fontWeight={600} textAnchor="middle" paintOrder="stroke" stroke="#FFFFFF" strokeWidth={3} strokeLinejoin="round" style={{ pointerEvents: "none" }}>{model.byId.get(hover)!.label}</text>;
          })()}
          {!coreOpen && (
            <text x={C} y={C + 32} fill={INK} fontSize={11} fontWeight={600} textAnchor="middle" paintOrder="stroke" stroke="#FFFFFF" strokeWidth={3.5} strokeLinejoin="round" style={{ pointerEvents: "none" }}>
              {core.length === 1 ? core[0].label : `${core.length} compromised packages`}
            </text>
          )}
        </g>
      </svg>
      <div className="space-y-4 text-[13px] leading-relaxed text-ink-2">
        <p>Rings count hops outward from the <span className="font-medium text-ink">{core.length} compromised package{core.length === 1 ? "" : "s"}</span> at the core to the services that pull them. Hover the core to fan them out.</p>
        <p><span className="font-medium text-signal-ink">Vermilion chains</span> end in a range that admits the malicious version. <span className="font-medium text-ink">Grey chains</span> reach the package, but their range excludes it.</p>
        {coreOpen ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }} className="border-y border-line py-2">
            <p className="mb-1.5 text-[12px] text-ink-3">At the core</p>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[12px]">
              {core.map((n) => <span key={n.id} className={clsx("truncate", hover === n.id ? "text-signal-ink" : "text-ink")}>{n.label}</span>)}
            </div>
          </motion.div>
        ) : (
          <div className="divide-y divide-line border-y border-line">
            {Array.from({ length: model.maxD }, (_, i) => i + 1).map((d) => {
              const svcs = model.nodes.filter((n) => n.service && n.d === d);
              return svcs.length ? (
                <div key={d} className="flex items-center justify-between py-1.5 tabular">
                  <span className="text-ink-3">{d} hop{d > 1 ? "s" : ""}</span>
                  <span><b className="font-semibold text-signal-ink">{svcs.filter((s) => s.exposed).length}</b> exposed, <b className="font-semibold text-ink">{svcs.filter((s) => !s.exposed).length}</b> safe</span>
                </div>
              ) : null;
            })}
          </div>
        )}
        <p className="text-[12px] text-ink-3">Hover a node to isolate its chain. One representative path per service and package.</p>
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
        <table className="border-separate border-spacing-[3px] text-[12px]">
          <thead>
            <tr>
              <th />
              {cols.map((c) => (
                <th key={c} className={clsx("align-bottom font-mono font-normal text-ink-2", cols.length > 8 && "h-24")}>{cols.length <= 8 ? <div className="pb-1 text-center">{c}</div> : <div className="w-6 origin-bottom-left translate-x-3 -rotate-45 whitespace-nowrap">{c}</div>}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s}>
                <td className="whitespace-nowrap pr-3 text-right text-ink-2">{s}</td>
                {cols.map((c) => {
                  const v = cell.get(`${s}|${c}`);
                  const op = v ? 0.25 + 0.75 * Math.sqrt(v.n / maxN) : 0;
                  return (
                    <td key={c} className="p-0">
                      <motion.div initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.002 * (rows.indexOf(s) * cols.length + cols.indexOf(c)) }}
                        onMouseMove={(ev) => v && setTip({ x: ev.clientX, y: ev.clientY, text: v.display, sub: `${v.reason} · ${v.n} path${v.n > 1 ? "s" : ""}` })}
                        onMouseLeave={() => setTip(null)}
                        className={clsx("h-6 rounded-[4px] transition-transform duration-150 ease-out hover:scale-110", cols.length <= 8 ? "w-14" : "w-6", !v && "bg-subtle", v && !v.exposed && "bg-surface ring-1 ring-inset ring-line-strong")}
                        style={v && v.exposed ? { background: `rgba(229,72,77,${op})` } : undefined} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-5 text-[12px] text-ink-3">
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-signal" />exposed, shade is the number of admitting paths</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] ring-1 ring-inset ring-line-strong" />reaches the package, range excludes it</span>
        <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-[3px] bg-subtle" />no path at this depth</span>
      </div>
      {tip && (
        <div className="pointer-events-none fixed z-50 max-w-md rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-md" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <p className="font-mono text-ink">{tip.text}</p>
          <p className="mt-0.5 text-ink-2">{tip.sub}</p>
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
  const W = 880, H = 260, P = 44;
  const maxR = Math.max(...pts.map((p) => p.reach)), minR = Math.min(...pts.map((p) => p.reach));
  const maxPr = Math.max(...pts.map((p) => p.pagerank), 1e-9);
  const x = (v: number) => P + ((v - minR + 1) / (maxR - minR + 2)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / maxPr) * (H - 2 * P);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      <line x1={P} y1={H - P} x2={W - P / 2} y2={H - P} stroke={LINE} />
      <line x1={P} y1={P / 2} x2={P} y2={H - P} stroke={LINE} />
      <text x={W - P / 2} y={H - 10} textAnchor="end" fill={STONE} fontSize="11">fleet reach (services) →</text>
      <text x={10} y={P / 2} fill={STONE} fontSize="11">PageRank ↑</text>
      {[minR, maxR].filter((v, i, a) => a.indexOf(v) === i).map((v) => (
        <text key={v} x={x(v)} y={H - P + 13} textAnchor="middle" fill={STONE} fontSize="10.5">{v}</text>
      ))}
      <text x={P - 5} y={y(maxPr) + 3} textAnchor="end" fill={STONE} fontSize="10.5">{maxPr.toFixed(2)}</text>
      <text x={P - 5} y={H - P + 3} textAnchor="end" fill={STONE} fontSize="10.5">0</text>
      <text x={P + 6} y={H - 10} fill={STONE} fontSize="10.5">ring size: packages maintained in the fleet</text>
      {pts.map((p, i) => {
        const cx = x(p.reach), cy = y(p.pagerank);
        const col = p.compromised_in_incident ? SIG : INK;
        return (
          <motion.g key={p.maintainer} initial={{ opacity: 0, scale: 0 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: i * 0.05 }} onMouseEnter={() => setHover(p.maintainer)} onMouseLeave={() => setHover(null)}>
            <circle cx={cx} cy={cy} r={Math.min(18, 6 + Math.sqrt(p.packages_in_fleet) * 1.6)} fill="none" stroke={col} strokeOpacity={hover === p.maintainer ? 0.6 : 0.22} />
            <circle cx={cx} cy={cy} r={hover === p.maintainer ? 5 : 4} fill={col} stroke="#FFFFFF" strokeWidth={1.5} />
            {(i < 5 || hover === p.maintainer) && <text x={cx + 8} y={cy - 6} fill={p.compromised_in_incident ? SIG : INK2} fontWeight={500} fontSize="11.5" paintOrder="stroke" stroke="#FFFFFF" strokeWidth={3}>{p.maintainer}</text>}
            {hover === p.maintainer && <text x={cx + 8} y={cy + 8} fill={STONE} fontSize="10.5" paintOrder="stroke" stroke="#FFFFFF" strokeWidth={3}>{p.reach} services · pr {p.pagerank.toFixed(2)} · {p.packages_in_fleet} pkgs</text>}
          </motion.g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ Incident timeline */
export type TimelineEvent = { kind: "compromise" | "advisory" | "campaign" | "incident" | "playbook"; at: string; title: string; count: number; items: string[]; ref: string | null };

const KIND = {
  compromise: { icon: Skull, color: "text-signal", ring: "ring-signal/40 bg-surface", label: "Account compromised" },
  advisory: { icon: FileWarning, color: "text-risk", ring: "ring-line-strong bg-surface", label: "Advisories published" },
  campaign: { icon: Skull, color: "text-risk", ring: "ring-line-strong bg-surface", label: "Malicious-package advisories published" },
  incident: { icon: Brain, color: "text-ink-2", ring: "ring-line-strong bg-surface", label: "Incident written to graph" },
  playbook: { icon: BookCheck, color: "text-safe", ring: "ring-line-strong bg-surface", label: "Playbook approved" },
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
    <ol className="relative ml-3 border-l border-line">
      {groups.map((g, i) => {
        const k = KIND[g.kind] ?? KIND.advisory;
        const Icon = k.icon;
        const first = g.items[0];
        const fmt = (s: string) => (s.length > 10 ? new Date(s).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : new Date(s + "T12:00:00Z").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }));
        return (
          <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i * 0.05, 0.4), duration: 0.25, ease: "easeOut" }} className="mb-7 ml-7">
            <span className={clsx("absolute -left-[13px] grid h-6 w-6 place-items-center rounded-full ring-1", k.ring)}><Icon className={clsx("h-3.5 w-3.5", k.color)} /></span>
            <div className="flex flex-wrap items-baseline gap-2">
              <p className="text-[14px] font-medium text-ink">{g.kind === "advisory" ? `${g.items.length} advisor${g.items.length > 1 ? "ies" : "y"} published` : k.label}</p>
              <span className="text-[12.5px] text-ink-3 tabular"><CalendarDays className="mr-1 inline h-3 w-3 -translate-y-px" />{fmt(first.at)}{g.items.length > 1 ? ` to ${fmt(g.items[g.items.length - 1].at).split(",").slice(-1)[0]}` : ""}</span>
            </div>
            {g.kind === "compromise" && <p className="mt-1 text-[13.5px] leading-relaxed text-ink-2">npm account <span className="font-mono text-ink">{first.title}</span> had publish rights on {first.count} packages that shipped malicious versions.</p>}
            {g.kind === "campaign" && <p className="mt-1 text-[13.5px] leading-relaxed text-ink-2"><span className="font-mono text-ink">{first.title}</span>: {first.count} OSV malicious-package advisories, first published this day. Sample:</p>}
            {g.kind === "incident" && g.items.map((e) => <p key={e.title} className="mt-1 text-[13.5px] leading-relaxed text-ink-2"><span className="font-mono text-ink-2">{e.title}</span>: {e.count} exposed services, trigger {e.items[0]}</p>)}
            {g.kind === "playbook" && g.items.map((e) => <p key={e.title + e.at} className="mt-1 text-[13.5px] leading-relaxed text-ink-2">{e.title}</p>)}
            {(g.kind === "advisory" || g.kind === "compromise" || g.kind === "campaign") && (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {(g.kind === "advisory" ? g.items.map((e) => e.items[0]) : first.items).map((t) => (
                  <span key={t} className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11.5px] text-ink-2">{t}</span>
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
  return <div className="grid place-items-center rounded-lg border border-dashed border-line-strong py-12 text-[13.5px] text-ink-3">{text}</div>;
}
