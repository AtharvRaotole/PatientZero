/* Radial layered layout for the fleet graph.
 * Services sit on the outer ring (grouped by team), and every package version sits on the ring for its hop distance
 * from the nearest service: direct dependencies just inside, deep transitive packages toward the core.
 * A node's angle follows the services that pull it in, so each service's dependency tree stays in its own sector. */

export type RadialNode = { id: string; kind: "Service" | "Version"; team?: string | null };
export type RadialEdge = { source: string; target: string };
export type Placement = { ang: number; r: number; depth: number };

/** Ring radius (fraction of the outer ellipse) per hop depth. Depth 5 and deeper share the core ring. */
export const RING_R = [1, 0.83, 0.67, 0.52, 0.38, 0.23];
export const MAX_RING = RING_R.length - 1;
export const RING_LABEL = ["services", "direct", "2 hops", "3 hops", "4 hops", "5+ hops"];

const TAU = Math.PI * 2;
const wrap = (a: number) => ((a % TAU) + TAU) % TAU;
const delta = (a: number, b: number) => {
  // signed smallest difference a - b in (-pi, pi]
  const d = wrap(a - b);
  return d > Math.PI ? d - TAU : d;
};

export function radialLayout(nodes: RadialNode[], edges: RadialEdge[]): Map<string, Placement> {
  const out = new Map<string, string[]>();
  const parents = new Map<string, string[]>();
  for (const e of edges) {
    (out.get(e.source) ?? out.set(e.source, []).get(e.source)!).push(e.target);
    (parents.get(e.target) ?? parents.set(e.target, []).get(e.target)!).push(e.source);
  }

  // services: grouped by team, half a slot of air between teams
  const services = nodes.filter((n) => n.kind === "Service").sort((a, b) => (a.team ?? "").localeCompare(b.team ?? "") || a.id.localeCompare(b.id));
  const teams = new Set(services.map((s) => s.team ?? ""));
  const slots = services.length + teams.size * 0.6;
  const place = new Map<string, Placement>();
  let cursor = 0, prevTeam: string | null = null;
  for (const s of services) {
    if (prevTeam !== null && s.team !== prevTeam) cursor += 0.6;
    place.set(s.id, { ang: wrap(-Math.PI / 2 + (cursor / slots) * TAU), r: 1, depth: 0 });
    prevTeam = s.team ?? "";
    cursor += 1;
  }

  // BFS hop depth from the services
  const depth = new Map<string, number>(services.map((s) => [s.id, 0]));
  let frontier = services.map((s) => s.id);
  while (frontier.length) {
    const next: string[] = [];
    for (const id of frontier) for (const t of out.get(id) ?? []) {
      if (!depth.has(t)) { depth.set(t, depth.get(id)! + 1); next.push(t); }
    }
    frontier = next;
  }

  // ring by ring: inherit the circular mean angle of shallower parents, then spread evenly while keeping the order
  const byRing = new Map<number, string[]>();
  for (const n of nodes) {
    if (n.kind === "Service") continue;
    const d = Math.min(depth.get(n.id) ?? MAX_RING, MAX_RING);
    (byRing.get(d) ?? byRing.set(d, []).get(d)!).push(n.id);
  }
  for (let d = 1; d <= MAX_RING; d++) {
    const ids = byRing.get(d) ?? [];
    if (!ids.length) continue;
    const target = new Map<string, number>();
    for (const id of ids) {
      let sx = 0, sy = 0;
      for (const p of parents.get(id) ?? []) {
        const pp = place.get(p);
        if (!pp) continue;
        sx += Math.cos(pp.ang); sy += Math.sin(pp.ang);
      }
      target.set(id, sx || sy ? wrap(Math.atan2(sy, sx)) : wrap(hash(id) * TAU));
    }
    ids.sort((a, b) => target.get(a)! - target.get(b)! || a.localeCompare(b));
    const n = ids.length;
    // best rotation of the uniform spread against the targets
    let ox = 0, oy = 0;
    ids.forEach((id, i) => {
      const off = delta(target.get(id)!, (i / n) * TAU);
      ox += Math.cos(off); oy += Math.sin(off);
    });
    const rot = Math.atan2(oy, ox);
    const crowded = n > 140; // split dense rings into two staggered bands so squares never touch
    ids.forEach((id, i) => {
      const uni = wrap((i / n) * TAU + rot);
      const ang = wrap(uni + 0.35 * delta(target.get(id)!, uni));
      const r = RING_R[d] + (crowded ? (i % 2 ? 0.022 : -0.022) : 0);
      place.set(id, { ang, r, depth: d });
    });
  }
  return place;
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}
