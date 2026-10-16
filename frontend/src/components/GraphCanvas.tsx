"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import type { FleetGraph, GraphElem } from "@/lib/types";

type Props = {
  /** Base graph (whole fleet). When null, `highlight` is rendered on its own. */
  fleet: FleetGraph | null;
  /** Subgraph to emphasize: nodes/edges with type (service | package | affected) and status (exposed | safe | path). */
  highlight: GraphElem | null;
  onNodeClick?: (id: string) => void;
  /** Reserve space on the left for an overlay panel (px). Defaults to the hero headline width on wide screens. */
  leftInset?: number;
  /** Height (px) covered by the headline overlay on narrow screens, where the graph sits below it. */
  topInset?: number;
};

type N = { id: string; kind: "Service" | "Version"; tier?: number | null; team?: string | null; x?: number; y?: number };

const C = {
  ink: "#0C0A09",
  ink2: "#57534E",
  stone: "#A8A29E",
  bg: "#D6D3D1",
  signal: "#E5484D",
  signalInk: "#C4282D",
  risk: "#B45309",
};

export default function GraphCanvas({ fleet, highlight, onNodeClick, leftInset, topInset }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const fg = useRef<any>(undefined);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Only depend on `highlight` in standalone mode, so highlighting never resets the fleet simulation.
  const standalone = fleet ? null : highlight;
  const data = useMemo(() => {
    if (!fleet && standalone) {
      const highlight = standalone;
      // Standalone mode: render any subgraph (e.g. a BYO scan) without the fleet underneath.
      const nodes: N[] = highlight.nodes.map((n) => ({ id: n.id, kind: n.type === "service" ? "Service" : "Version" }));
      const ids = new Set(nodes.map((n) => n.id));
      const links = highlight.edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ source: e.source, target: e.target, requirement: e.requirement, key: `${e.source}->${e.target}` }));
      return { nodes, links };
    }
    if (!fleet) return { nodes: [] as N[], links: [] as any[] };
    const nodes: N[] = fleet.nodes.map((n) => ({ id: n.id, kind: n.kind, tier: n.tier, team: n.team }));
    const ids = new Set(nodes.map((n) => n.id));
    const links = fleet.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, requirement: e.requirement, key: `${e.source}->${e.target}` }));
    return { nodes, links };
  }, [fleet, standalone]);

  const hl = useMemo(() => {
    const nodes = new Map<string, { type: string; status?: string; bad?: string }>();
    const links = new Map<string, { status: string; last: boolean; requirement: string | null }>();
    highlight?.nodes.forEach((n) => nodes.set(n.id, n));
    highlight?.edges.forEach((e) => links.set(`${e.source}->${e.target}`, e));
    return { nodes, links, active: nodes.size > 0 };
  }, [highlight]);

  // physics tuning
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    g.d3Force("charge")?.strength(-22).distanceMax(260);
    g.d3Force("link")?.distance((l: any) => (l.source?.kind === "Service" ? 34 : 16));
  }, [data]);

  // camera: frame the highlighted subgraph (or the whole fleet) in the area right of the headline panel.
  // Standalone subgraphs (BYO scans) have no overlay, so they use the full canvas and refit when the layout settles.
  const isStandalone = !fleet;
  const fit = useCallback((ms = 900) => {
    const g = fg.current;
    if (!g || !data.nodes.length) return;
    const filter = hl.active && !isStandalone ? (n: N) => hl.nodes.has(n.id) : () => true;
    const bb = g.getGraphBbox(filter);
    if (!bb) return;
    const leftPad = isStandalone ? 0 : leftInset ?? (size.w >= 1100 ? Math.min(560, size.w * 0.42) : 0);
    const overlay = isStandalone || leftPad ? 0 : topInset ?? 260;
    const pad = isStandalone ? 40 : 70;
    const bw = Math.max(bb.x[1] - bb.x[0], 1);
    const bh = Math.max(bb.y[1] - bb.y[0], 1);
    const availW = size.w - leftPad - pad * 2;
    const availH = size.h - pad * 2 - overlay;
    const z = Math.max(0.4, Math.min(isStandalone ? 1.8 : hl.active ? 3.2 : 2.2, availW / bw, availH / bh));
    const cx = (bb.x[0] + bb.x[1]) / 2 - leftPad / 2 / z;
    const cy = (bb.y[0] + bb.y[1]) / 2 - overlay / 2 / z;
    g.centerAt(cx, cy, ms);
    g.zoom(z, ms);
  }, [hl, data, size.w, size.h, leftInset, topInset, isStandalone]);

  useEffect(() => {
    const t = setTimeout(() => fit(), hl.active ? 150 : 1800);
    return () => clearTimeout(t);
  }, [fit, hl.active]);

  const colorOf = (n: N): string => {
    const h = hl.nodes.get(n.id);
    if (h) {
      if (h.status === "path") return C.ink;
      if (h.type === "affected" || h.type === "service") return h.status === "exposed" ? C.signal : C.stone;
      return h.status === "exposed" ? C.risk : C.stone;
    }
    return n.kind === "Service" ? C.ink : C.bg;
  };

  // Neighbors (for hover) and link objects by key (force-graph mutates data.links in place).
  const { neighbors } = useMemo(() => {
    const neighbors = new Map<string, Set<string>>();
    const linkByKey = new Map<string, any>();
    for (const l of data.links as any[]) {
      const a = typeof l.source === "object" ? l.source.id : l.source;
      const b = typeof l.target === "object" ? l.target.id : l.target;
      if (!neighbors.has(a)) neighbors.set(a, new Set());
      if (!neighbors.has(b)) neighbors.set(b, new Set());
      neighbors.get(a)!.add(b);
      neighbors.get(b)!.add(a);
      linkByKey.set(l.key, l);
    }
    return { neighbors, linkByKey };
  }, [data]);

  // Draw order: hop distance of each exposed edge from the compromised version, so exposed paths draw in from the source outward.
  const wave = useMemo(() => {
    const dist = new Map<string, number>();
    const parents = new Map<string, string[]>(); // dependency -> dependents, exposed edges only
    hl.links.forEach((e, k) => {
      if (e.status !== "exposed") return;
      const [src, tgt] = k.split("->");
      if (!parents.has(tgt)) parents.set(tgt, []);
      parents.get(tgt)!.push(src);
    });
    const q: string[] = [];
    hl.nodes.forEach((n, id) => {
      if (n.type === "affected" && n.status === "exposed") {
        dist.set(id, 0);
        q.push(id);
      }
    });
    while (q.length) {
      const id = q.shift()!;
      for (const p of parents.get(id) ?? []) {
        if (!dist.has(p)) {
          dist.set(p, dist.get(id)! + 1);
          q.push(p);
        }
      }
    }
    const maxD = Math.max(1, ...dist.values());
    return { dist, maxD };
  }, [hl]);

  // Hover focus: a service lights up its dependency chain (within the highlight when one is shown); anything else its neighbors.
  const focus = useMemo(() => {
    if (!hover) return null;
    const nodes = new Set<string>([hover]);
    const edges = new Set<string>();
    const node = (data.nodes as N[]).find((n) => n.id === hover);
    if (node?.kind === "Service") {
      const out = new Map<string, { to: string; key: string }[]>();
      for (const l of data.links as any[]) {
        if (hl.active && !hl.links.has(l.key)) continue;
        const a = typeof l.source === "object" ? l.source.id : l.source;
        const b = typeof l.target === "object" ? l.target.id : l.target;
        if (!out.has(a)) out.set(a, []);
        out.get(a)!.push({ to: b, key: l.key });
      }
      const maxHops = hl.active ? 12 : 3;
      let frontier = [hover];
      for (let d = 0; d < maxHops && frontier.length; d++) {
        const next: string[] = [];
        for (const id of frontier) for (const e of out.get(id) ?? []) {
          edges.add(e.key);
          if (!nodes.has(e.to)) { nodes.add(e.to); next.push(e.to); }
        }
        frontier = next;
      }
    } else {
      for (const nb of neighbors.get(hover) ?? []) nodes.add(nb);
      for (const l of data.links as any[]) {
        const a = typeof l.source === "object" ? l.source.id : l.source;
        const b = typeof l.target === "object" ? l.target.id : l.target;
        if (a === hover || b === hover) edges.add(l.key);
      }
    }
    return { nodes, edges, at: performance.now() };
  }, [hover, data, hl, neighbors]);

  const reduced = useMemo(() => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches, []);
  const font = useMemo(() => (typeof window !== "undefined" ? getComputedStyle(document.body).fontFamily : "system-ui"), []);
  const intro = useRef<{ start: number; cx: number; cy: number; span: number } | null>(null);
  const introProgress = (now: number) => (reduced || !intro.current ? 1 : Math.min(1, (now - intro.current.start) / 1400));
  const revealOf = (n: any, now: number) => {
    const it = intro.current;
    if (reduced || !it) return 1;
    const d = Math.min(1, Math.hypot((n.x ?? 0) - it.cx, (n.y ?? 0) - it.cy) / it.span) || 0;
    return smooth((now - it.start - 60 - d * 800) / 360);
  };

  // Restart the sequential draw-in whenever the highlighted subgraph changes (after the camera has started to move).
  const drawStart = useRef(0);
  useEffect(() => {
    drawStart.current = performance.now() + 250;
  }, [hl]);
  const HOP_MS = 260;
  const edgeProgress = (k: string, src: string, now: number, status: string) => {
    if (reduced) return 1;
    const t = now - drawStart.current;
    if (status !== "exposed") return smooth(t / 500);
    const d = (wave.dist.get(src) ?? wave.maxD) - 1;
    return smooth((t - Math.max(0, d) * HOP_MS) / HOP_MS);
  };
  const focusMix = (now: number) => (focus ? (reduced ? 1 : smooth((now - focus.at) / 180)) : 0);

  const onFramePre = useCallback(() => {
    if (intro.current || !data.nodes.length) return;
    const ns = data.nodes as any[];
    let cx = 0, cy = 0;
    for (const n of ns) { cx += n.x ?? 0; cy += n.y ?? 0; }
    cx /= ns.length; cy /= ns.length;
    let span = 1;
    for (const n of ns) span = Math.max(span, Math.hypot((n.x ?? 0) - cx, (n.y ?? 0) - cy));
    intro.current = { start: performance.now(), cx, cy, span };
    fit(0); // frame the graph instantly on the first frame, so the reveal happens where it will stay
  }, [data, fit]);

  // Labels: services, focused paths and the hover chain only. Collision-checked, on a paper plate.
  const onFramePost = useCallback((ctx: CanvasRenderingContext2D, scale: number) => {
    const now = performance.now();
    const fm = focusMix(now);
    const cands: { n: any; prio: number }[] = [];
    for (const n of data.nodes as any[]) {
      if (n.x === undefined) continue;
      const h = hl.nodes.get(n.id);
      const isSvc = n.kind === "Service";
      let prio = -1;
      if (n.id === hover) prio = 0;
      else if (focus?.nodes.has(n.id)) prio = isSvc ? 1 : 2;
      else if (hl.active) {
        if (h && isSvc) prio = h.status === "exposed" ? 3 : 5;
        else if (h && (h.type === "affected" || h.status === "path")) prio = 4;
      } else if (isSvc) prio = 3;
      if (prio >= 0) cands.push({ n, prio });
    }
    cands.sort((a, b) => a.prio - b.prio);
    const placed: [number, number, number, number][] = [];
    const ip = introProgress(now);
    let count = 0;
    for (const { n, prio } of cands) {
      if (count >= 48) break;
      const isSvc = n.kind === "Service";
      const fs = (prio === 0 ? 12.5 : 11.5) / scale;
      ctx.font = `${isSvc || prio === 0 ? 550 : 450} ${fs}px ${font}`;
      const w = ctx.measureText(n.id).width;
      const padX = 4 / scale, padY = 2 / scale;
      const r = isSvc ? 4.5 : 3;
      const x0 = n.x - w / 2 - padX, y0 = n.y + r + 3 / scale, bw = w + padX * 2, bh = fs * 1.15 + padY * 2;
      if (prio > 0 && placed.some(([x, y, ww, hh]) => x0 < x + ww && x0 + bw > x && y0 < y + hh && y0 + bh > y)) continue;
      placed.push([x0, y0, bw, bh]);
      count++;
      const inFocus = !focus || focus.nodes.has(n.id);
      ctx.globalAlpha = revealOf(n, now) * ip * (inFocus ? 1 : 1 - 0.85 * fm);
      ctx.fillStyle = "rgba(250,250,249,0.88)";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x0, y0, bw, bh, 3 / scale);
      else ctx.rect(x0, y0, bw, bh);
      ctx.fill();
      const h = hl.nodes.get(n.id);
      ctx.fillStyle = h?.status === "exposed" && (isSvc || h.type === "affected") ? C.signalInk : isSvc || prio <= 1 ? C.ink : C.ink2;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(n.id, n.x, y0 + padY);
      ctx.globalAlpha = 1;
    }
  }, [data, hl, hover, focus, font, reduced]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={wrap} className="absolute inset-0">
      <ForceGraph2D
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        warmupTicks={60}
        cooldownTicks={220}
        onEngineStop={() => isStandalone && fit(600)}
        autoPauseRedraw={false}
        enableNodeDrag
        minZoom={0.3}
        maxZoom={12}
        nodeRelSize={1}
        onRenderFramePre={onFramePre}
        onRenderFramePost={onFramePost}
        nodeVal={(n: any) => (n.kind === "Service" ? 20 : hl.nodes.has(n.id) ? 9 : 2.2)}
        nodeLabel={(n: any) => tooltip(n, hl.nodes.get(n.id))}
        onNodeHover={(n: any) => {
          setHover(n?.id ?? null);
          if (wrap.current) wrap.current.style.cursor = n ? "pointer" : "";
        }}
        onNodeClick={(n: any) => {
          fg.current?.centerAt(n.x, n.y, 700);
          fg.current?.zoom(4, 700);
          onNodeClick?.(n.id);
        }}
        linkColor={(l: any) => {
          const now = performance.now();
          const ip = introProgress(now);
          const fm = focusMix(now);
          if (focus?.edges.has(l.key)) return `rgba(12,10,9,${(0.12 + 0.43 * fm) * ip})`;
          const base = hl.active ? 0.05 : 0.16;
          return `rgba(120,113,108,${base * ip * (focus ? 1 - 0.7 * fm : 1)})`;
        }}
        linkWidth={(l: any) => (focus?.edges.has(l.key) ? 0.9 : 0.5)}
        linkCanvasObjectMode={(l: any) => (hl.links.has(l.key) ? "replace" : undefined)}
        linkCanvasObject={(l: any, ctx: CanvasRenderingContext2D, scale: number) => {
          const h = hl.links.get(l.key);
          if (!h || typeof l.source !== "object") return;
          const now = performance.now();
          const p = edgeProgress(l.key, l.source.id, now, h.status);
          if (p <= 0) return;
          const fm = focusMix(now);
          const inFocus = !focus || focus.edges.has(l.key);
          const exposed = h.status === "exposed";
          // draw from the dependency (target) toward the dependent (source), like a stroke-dashoffset reveal
          const tx = l.target.x, ty = l.target.y, sx = l.source.x, sy = l.source.y;
          const hx = tx + (sx - tx) * p, hy = ty + (sy - ty) * p;
          let a = exposed ? 0.85 : h.status === "path" ? 0.8 : 0.45;
          if (exposed && !reduced && p >= 1) a *= 0.82 + 0.18 * Math.sin((now - drawStart.current) / 700 - (wave.dist.get(l.source.id) ?? 0) * 0.6);
          a *= inFocus ? 1 : 1 - 0.85 * fm;
          ctx.globalAlpha = a;
          ctx.strokeStyle = exposed ? C.signal : h.status === "path" ? C.ink : C.stone;
          ctx.lineWidth = (exposed ? 1.6 : 1) / Math.max(scale * 0.55, 0.7);
          ctx.lineCap = "round";
          ctx.beginPath();
          ctx.moveTo(tx, ty);
          ctx.lineTo(hx, hy);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }}
        nodeCanvasObject={(n: any, ctx: CanvasRenderingContext2D, scale: number) => {
          const now = performance.now();
          const h = hl.nodes.get(n.id);
          const isSvc = n.kind === "Service";
          const reveal = revealOf(n, now);
          if (reveal <= 0.001) return;
          const fm = focusMix(now);
          const inFocus = focus ? focus.nodes.has(n.id) : false;
          const dimByFocus = focus && !inFocus ? 1 - 0.85 * fm : 1;
          const faded = hl.active && !h && !inFocus;
          const col = colorOf(n);
          if (!isSvc && !h && !inFocus) {
            // cheap path for the ~2,700 background versions
            ctx.globalAlpha = reveal * (faded ? 0.4 : 1) * dimByFocus;
            ctx.fillStyle = C.bg;
            ctx.fillRect(n.x - 1.4, n.y - 1.4, 2.8, 2.8);
            ctx.globalAlpha = 1;
            return;
          }
          // a node on an exposed path appears once its edge has drawn in
          let arrive = 1;
          if (h && h.status === "exposed" && !reduced) {
            const d = wave.dist.get(n.id);
            if (d !== undefined && d > 0) arrive = smooth((now - drawStart.current - d * HOP_MS + 40) / 160);
          }
          ctx.globalAlpha = reveal * dimByFocus * (faded ? (isSvc ? 0.45 : 0.3) : 1) * (h?.status === "exposed" ? 0.25 + 0.75 * arrive : 1);
          const hovered = hover === n.id;
          const r = (isSvc ? 4.5 : h || inFocus ? 2.8 : 2) * (hovered ? 1.25 : 1);
          const hollow = isSvc && h?.status === "safe";
          ctx.beginPath();
          ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
          ctx.fillStyle = hollow ? "#FFFFFF" : !h && !isSvc ? C.stone : col;
          ctx.fill();
          ctx.lineWidth = (hollow ? 1.4 : 1.2) / Math.max(scale * 0.6, 0.7);
          ctx.strokeStyle = hollow ? C.ink : "#FAFAF9";
          ctx.stroke();
          // slow, subtle pulse on exposed services and the compromised version
          if (h && h.status === "exposed" && (h.type === "service" || h.type === "affected") && !reduced && arrive >= 1) {
            const t = ((now - drawStart.current) % 2600) / 2600;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 1.5 + t * 7, 0, 2 * Math.PI);
            ctx.strokeStyle = `rgba(229,72,77,${0.35 * (1 - t) * dimByFocus})`;
            ctx.lineWidth = 1 / Math.max(scale * 0.6, 0.7);
            ctx.stroke();
          }
          if (hovered) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 3, 0, 2 * Math.PI);
            ctx.strokeStyle = "rgba(12,10,9,0.35)";
            ctx.lineWidth = 1 / Math.max(scale * 0.6, 0.7);
            ctx.stroke();
          }
          ctx.globalAlpha = 1;
        }}
        nodePointerAreaPaint={(n: any, color: string, ctx: CanvasRenderingContext2D) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.kind === "Service" ? 8 : 4, 0, 2 * Math.PI);
          ctx.fill();
        }}
      />
    </div>
  );
}

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function tooltip(n: N, h?: { type: string; status?: string; bad?: string }): string {
  const kind = n.kind === "Service" ? `Service, tier ${n.tier ?? "?"}${n.team ? `, ${esc(n.team)}` : ""}` : "Package version";
  const st = h?.status === "exposed" ? `<div style="color:#C4282D;margin-top:4px">Exposed${h.bad ? `: ${esc(h.bad)}` : ""}</div>`
    : h?.status === "safe" ? `<div style="color:#57534E;margin-top:4px">Range excludes the bad version</div>`
    : h?.status === "path" ? `<div style="color:#0C0A09;margin-top:4px">On the shortest path</div>` : "";
  return `<div style="font-family:inherit;background:#FFFFFF;border:1px solid #E7E5E4;padding:8px 10px;border-radius:8px;font-size:12px;color:#0C0A09;box-shadow:0 4px 16px rgba(12,10,9,.08)"><div style="font-weight:600;font-family:var(--font-geist-mono),ui-monospace,monospace">${esc(n.id)}</div><div style="color:#A8A29E;font-size:11.5px;margin-top:2px">${kind}</div>${st}</div>`;
}
