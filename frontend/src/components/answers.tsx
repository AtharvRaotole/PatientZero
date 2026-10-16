"use client";
import { motion } from "framer-motion";
import clsx from "clsx";
import { BellRing, Check, ShieldCheck } from "lucide-react";
import type { AskResponse, FleetGraph, Person, WakeEntry } from "@/lib/types";
import { Card, CardHeader, CopyButton } from "./ui";

const EASE = [0.22, 1, 0.36, 1] as const;
const initials = (n: string) => n.split(" ").map((p) => p[0]).join("").slice(0, 2);

/** Display name of what was checked: the campaign name, else the trigger. */
export function checkedName(r: AskResponse): string {
  return r.entity?.campaign?.[1] ?? r.trigger?.replace(/^campaign\s+/i, "") ?? r.entity_key ?? r.query;
}

/** A clean answer: something was checked and no service can install it. */
export const isNotHit = (r: AskResponse) => r.outcome === "not_in_graph" && r.clean.length > 0 && r.exposed.length === 0;

// ------------------------------------------------------------------ Who to wake

/** One responder card per service: owner, channels and the people to page, with the ownership subgraph. */
export function ResponderView({ wake }: { wake: WakeEntry[] }) {
  if (!wake.length) return null;
  return (
    <div className="space-y-4" data-testid="responder">
      {wake.map((w, i) => <Responder key={w.service} w={w} delay={i * 0.05} />)}
    </div>
  );
}

function Responder({ w, delay }: { w: WakeEntry; delay: number }) {
  const oncallHandles = new Set(w.oncall.map((o) => o.handle));
  const role = (h: string) => w.members.find((m) => m.handle === h)?.role;
  const others = w.members.filter((m) => !oncallHandles.has(m.handle));
  return (
    <Card delay={delay}>
      <CardHeader icon={<BellRing />} title={`Who to wake for ${w.service}`} sub="Synthetic Acme org. Page the on-call first, then the team channel." />
      <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-mono text-[18px] font-semibold tracking-[-0.01em] text-ink">{w.service}</h3>
            <Chip>Tier {w.tier}</Chip>
            {w.env && <Chip>{w.env}</Chip>}
            {w.repo && <span className="font-mono text-[12.5px] text-ink-3">{w.repo}</span>}
          </div>
          <dl className="mt-4 grid grid-cols-[88px_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-[13.5px]">
            <dt className="text-ink-3">Team</dt>
            <dd className="text-ink">{w.team_display} <span className="font-mono text-[12.5px] text-ink-3">{w.team}</span></dd>
            <dt className="text-ink-3">Slack</dt>
            <dd className="flex items-center gap-2"><span className="font-mono text-ink">{w.slack}</span><CopyButton text={w.slack} label="Copy" /></dd>
            <dt className="text-ink-3">Pager</dt>
            <dd className="flex items-center gap-2"><span className="font-mono text-ink">{w.pager}</span><CopyButton text={w.pager} label="Copy" /></dd>
          </dl>
          <p className="mb-2 mt-5 text-[12.5px] font-medium text-ink-2">On call now</p>
          <div className="space-y-2">
            {w.oncall.length ? w.oncall.map((o) => (
              <div key={o.handle} className="flex items-center gap-3 rounded-lg border border-line bg-subtle/60 px-3.5 py-2.5">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-[11.5px] font-semibold text-paper">{initials(o.name)}</span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-medium text-ink">{o.name} <span className="font-normal text-ink-3">{o.handle}</span></p>
                  <p className="truncate text-[12.5px] text-ink-3">{[role(o.handle), o.email].filter(Boolean).join(" · ")}</p>
                </div>
                {o.email && <CopyButton text={o.email} label="Email" />}
              </div>
            )) : <p className="text-[13px] text-ink-3">Nobody is on call for this team. Use the channel.</p>}
          </div>
          {others.length > 0 && (
            <>
              <p className="mb-2 mt-5 text-[12.5px] font-medium text-ink-2">Also on the team</p>
              <div className="flex flex-wrap gap-x-5 gap-y-2">
                {others.map((m) => (
                  <span key={m.handle} className="flex items-center gap-2 text-[13px] text-ink">
                    <span className="grid h-6 w-6 place-items-center rounded-full bg-subtle text-[10.5px] font-semibold text-ink-2 ring-1 ring-line">{initials(m.name)}</span>
                    {m.name} <span className="text-ink-3">{m.role ?? m.handle}</span>
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="min-w-0 rounded-lg border border-line bg-paper/60 p-3">
          <p className="mb-1 px-1 text-[12px] text-ink-3">From the graph: <span className="font-mono">(:Team)-[:OWNS]-&gt;(:Service)</span>, on-call and members</p>
          <OwnershipGraph w={w} />
        </div>
      </div>
    </Card>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-md bg-subtle px-1.5 py-0.5 text-[12px] font-medium text-ink-2 ring-1 ring-inset ring-line">{children}</span>;
}

/** Service, team and people as a tiny left-to-right graph. On-call people are filled, other members hollow. */
function OwnershipGraph({ w }: { w: WakeEntry }) {
  const oncall = new Set(w.oncall.map((o) => o.handle));
  const people: (Person & { on: boolean })[] = [
    ...w.oncall.map((o) => ({ ...o, on: true })),
    ...w.members.filter((m) => !oncall.has(m.handle)).map((m) => ({ ...m, on: false })),
  ].slice(0, 6);
  const W = 330, rowH = 44, PX = 236, H = Math.max(150, people.length * rowH + 36);
  const svc = { x: 40, y: H / 2 }, team = { x: 140, y: H / 2 };
  const py = (i: number) => H / 2 + (i - (people.length - 1) / 2) * rowH;
  const draw = (d: number) => ({ initial: { pathLength: 0, opacity: 0 }, animate: { pathLength: 1, opacity: 1 }, transition: { duration: 0.45, delay: d, ease: EASE } });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`${w.team_display} owns ${w.service}; ${people.map((p) => p.name).join(", ")}`}>
      <motion.line x1={team.x - 36} y1={team.y} x2={svc.x + 9} y2={svc.y} stroke="#0C0A09" strokeWidth={1.4} {...draw(0.05)} />
      <text x={(svc.x + team.x - 36) / 2 + 4} y={svc.y - 8} textAnchor="middle" className="fill-ink-3 font-mono" fontSize={10.5}>OWNS</text>
      {people.map((p, i) => (
        <g key={p.handle}>
          <motion.path d={`M ${team.x + 36} ${team.y} C ${team.x + 90} ${team.y}, ${PX - 50} ${py(i)}, ${PX - 8} ${py(i)}`} fill="none"
            stroke={p.on ? "#0C0A09" : "#A8A29E"} strokeWidth={p.on ? 1.4 : 1} strokeDasharray={p.on ? undefined : "3 3"} {...draw(0.2 + i * 0.06)} />
        </g>
      ))}
      <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }}>
        <circle cx={svc.x} cy={svc.y} r={9} fill="#0C0A09" />
        <text x={svc.x} y={svc.y + 26} textAnchor="middle" className="fill-ink font-mono" fontSize={12.5} fontWeight={600}>{w.service}</text>
        <text x={svc.x} y={svc.y + 40} textAnchor="middle" className="fill-ink-3" fontSize={11}>tier {w.tier}</text>
        <rect x={team.x - 36} y={team.y - 15} width={72} height={30} rx={8} fill="#FFFFFF" stroke="#0C0A09" strokeWidth={1.2} />
        <text x={team.x} y={team.y + 4} textAnchor="middle" className="fill-ink font-mono" fontSize={12}>{w.team}</text>
        <text x={team.x} y={team.y + 30} textAnchor="middle" className="fill-ink-3 font-mono" fontSize={11}>{w.slack}</text>
      </motion.g>
      {people.map((p, i) => (
        <motion.g key={p.handle} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2, delay: 0.35 + i * 0.06 }}>
          <circle cx={PX} cy={py(i)} r={6.5} fill={p.on ? "#0C0A09" : "#FFFFFF"} stroke="#0C0A09" strokeWidth={1.2} />
          <text x={PX + 13} y={py(i) - 1} className="fill-ink" fontSize={13} fontWeight={p.on ? 600 : 400}>{p.name.split(" ")[0]}</text>
          <text x={PX + 13} y={py(i) + 13} className="fill-ink-3 font-mono" fontSize={10}>{p.on ? "ONCALL_FOR" : "MEMBER_OF"}</text>
        </motion.g>
      ))}
    </svg>
  );
}

// ------------------------------------------------------------------ Not hit

/** A confident clean answer: what was checked, how much of it, and every service that came back clean. */
export function NotHitView({ r, fleet }: { r: AskResponse; fleet: FleetGraph | null }) {
  if (!isNotHit(r)) return null;
  const name = checkedName(r);
  const packages = Array.from(new Set(r.scope.map((s) => s.package)));
  const versions = r.scope.length;
  const total = r.fleet_size ?? r.clean.length;
  const meta = new Map((fleet?.nodes ?? []).filter((n) => n.kind === "Service").map((n) => [n.id, n]));
  const services = [...r.clean].sort((a, b) => (meta.get(a)?.tier ?? 9) - (meta.get(b)?.tier ?? 9) || a.localeCompare(b));
  const sample = packages.slice(0, 8);
  return (
    <Card>
      <CardHeader icon={<ShieldCheck />} title={`${name}: not hit`} sub={`Every ${r.entity?.campaign ? "campaign " : ""}version checked against every service, up to depth ${r.depth}.`} />
      <div className="grid grid-cols-3 divide-x divide-line border-b border-line" data-testid="not-hit-stats">
        <Stat value={packages.length} label={packages.length === 1 ? "package checked" : "packages checked"} />
        <Stat value={versions} label={versions === 1 ? "malicious version" : "malicious versions"} />
        <Stat value={0} of={total} label="services can install one" safe />
      </div>
      <div className="p-5">
        <p className="mb-3 text-[12.5px] font-medium text-ink-2">{services.length} clean services</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" data-testid="clean-grid">
          {services.map((s, i) => {
            const m = meta.get(s);
            return (
              <motion.div key={s} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, delay: 0.04 + i * 0.015, ease: EASE }}
                className="flex min-w-0 items-center gap-2.5 rounded-lg border border-safe/15 bg-safe-soft px-3 py-2">
                <Check className="h-3.5 w-3.5 shrink-0 text-safe" strokeWidth={2.5} />
                <div className="min-w-0">
                  <p className="truncate font-mono text-[12.5px] text-ink">{s}</p>
                  {m && <p className="truncate text-[11.5px] text-ink-3">Tier {m.tier ?? "?"}{m.team ? ` · ${m.team}` : ""}</p>}
                </div>
              </motion.div>
            );
          })}
        </div>
        {sample.length > 0 && (
          <div className="mt-5 border-t border-line pt-4">
            <p className="mb-2 text-[12.5px] text-ink-3">Checked, for example</p>
            <div className="flex flex-wrap gap-1.5">
              {sample.map((p) => <span key={p} className="rounded-md bg-subtle px-1.5 py-0.5 font-mono text-[12px] text-ink-2 ring-1 ring-inset ring-line">{p}</span>)}
              {packages.length > sample.length && <span className="px-1 py-0.5 text-[12px] text-ink-3">and {packages.length - sample.length} more</span>}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function Stat({ value, of, label, safe }: { value: number; of?: number; label: string; safe?: boolean }) {
  return (
    <div className="px-5 py-4">
      <p className="text-[26px] font-semibold leading-none tracking-[-0.02em] text-ink tabular">
        <span className={clsx(safe && "text-safe")}>{value.toLocaleString()}</span>
        {of !== undefined && <span className="text-[16px] font-medium text-ink-3"> of {of}</span>}
      </p>
      <p className="mt-1.5 text-[12.5px] text-ink-3">{label}</p>
    </div>
  );
}
