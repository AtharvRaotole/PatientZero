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
  service: "#7c9cff",
  exposed: "#ff4d6d",
  safe: "#34d399",
  chain: "#fbbf24",
  path: "#a78bfa",
  dim: "rgba(148,163,184,0.45)",
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
      if (h.status === "path") return n.kind === "Service" ? C.service : C.path;
      if (h.type === "affected") return h.status === "exposed" ? C.exposed : C.safe;
      if (h.type === "service") return h.status === "exposed" ? C.exposed : C.safe;
      return h.status === "exposed" ? C.chain : "rgba(52,211,153,0.8)";
    }
    return n.kind === "Service" ? C.service : C.dim;
  };

  // Neighbors (for hover) and link objects by key (force-graph mutates data.links in place).
  const { neighbors, linkByKey } = useMemo(() => {
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

  // Propagation wave: hop distance of each highlighted node from the compromised (affected) versions.
  const wave = useMemo(() => {
    const dist = new Map<string, number>();
    const parents = new Map<string, string[]>(); // dependency -> dependents, exposed edges only
    const exposedKeys: string[] = [];
    hl.links.forEach((e, k) => {
      if (e.status !== "exposed") return;
      const [src, tgt] = k.split("->");
      if (!parents.has(tgt)) parents.set(tgt, []);
      parents.get(tgt)!.push(src);
      exposedKeys.push(k);
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
    return { dist, exposedKeys, maxD };
  }, [hl]);

  const reduced = useMemo(() => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches, []);
  const intro = useRef<{ start: number; cx: number; cy: number; span: number } | null>(null);
  const introProgress = (now: number) => (reduced || !intro.current ? 1 : Math.min(1, (now - intro.current.start) / 1600));
  const revealOf = (n: any, now: number) => {
    const it = intro.current;
    if (reduced || !it) return 1;
    const d = Math.min(1, Math.hypot((n.x ?? 0) - it.cx, (n.y ?? 0) - it.cy) / it.span) || 0; // 0 center .. 1 edge
    return smooth((now - it.start - 80 - d * 900) / 420);
  };

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

  const onFramePost = useCallback((ctx: CanvasRenderingContext2D, scale: number) => {
    const now = performance.now();
    // 1. propagation pulses on exposed edges, travelling from the compromised package toward the services
    if (!reduced && wave.exposedKeys.length) {
      const hop = 620;
      const cycle = (wave.maxD + 2.2) * hop;
      const phase = (now % cycle) / hop;
      for (const k of wave.exposedKeys) {
        const l = linkByKey.get(k);
        if (!l || typeof l.source !== "object") continue;
        const dt = wave.dist.get(l.target.id);
        if (dt === undefined) continue;
        const p = phase - dt;
        if (p < 0 || p >= 1) continue;
        const e = easeInOut(p);
        const tx = l.target.x, ty = l.target.y, sx = l.source.x, sy = l.source.y;
        const hx = tx + (sx - tx) * e, hy = ty + (sy - ty) * e;
        const tail = Math.max(0, e - 0.28);
        const bx = tx + (sx - tx) * tail, by = ty + (sy - ty) * tail;
        const g = ctx.createLinearGradient(bx, by, hx, hy);
        g.addColorStop(0, "rgba(255,77,109,0)");
        g.addColorStop(1, "rgba(255,180,195,0.95)");
        ctx.strokeStyle = g;
        ctx.lineWidth = 2.4 / Math.max(scale, 0.8);
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.lineTo(hx, hy);
        ctx.stroke();
        const r = 2.2 / Math.max(scale * 0.6, 0.7);
        ctx.drawImage(glowSprite("#ff4d6d"), hx - r * 3, hy - r * 3, r * 6, r * 6);
        ctx.fillStyle = "#ffe4ea";
        ctx.beginPath();
        ctx.arc(hx, hy, r * 0.55, 0, 2 * Math.PI);
        ctx.fill();
      }
    }
    // 2. labels: prioritized, collision-checked, on a backing plate
    const hoverSet = hover ? neighbors.get(hover) : null;
    const cands: { n: any; prio: number }[] = [];
    for (const n of data.nodes as any[]) {
      if (n.x === undefined) continue;
      const h = hl.nodes.get(n.id);
      const isSvc = n.kind === "Service";
      let prio = -1;
      if (n.id === hover) prio = 0;
      else if (hoverSet?.has(n.id)) prio = 1;
      else if (hl.active) {
        if (h && h.status === "exposed" && (isSvc || h.type === "affected")) prio = isSvc ? 2 : 3;
        else if (h && (isSvc || h.status === "path")) prio = 4;
        else if (h && scale > 2.6) prio = 5;
        else if (isSvc && scale > 1.6) prio = 7;
      } else {
        if (isSvc) prio = 2;
        else if (scale > 3.6) prio = 8;
      }
      if (prio >= 0) cands.push({ n, prio });
    }
    cands.sort((a, b) => a.prio - b.prio);
    const placed: [number, number, number, number][] = [];
    const ip = introProgress(now);
    let count = 0;
    for (const { n, prio } of cands) {
      if (count >= 64) break;
      const isSvc = n.kind === "Service";
      const fsPx = prio <= 1 ? 12 : isSvc ? 11 : 10;
      const fs = fsPx / scale;
      ctx.font = `${isSvc || prio <= 3 ? 600 : 500} ${fs}px var(--font-geist-sans), system-ui, sans-serif`;
      const w = ctx.measureText(n.id).width;
      const padX = 4 / scale, padY = 2 / scale;
      const r = isSvc ? 5 : hl.nodes.has(n.id) ? 3 : 1.5;
      const x0 = n.x - w / 2 - padX, y0 = n.y + r + 3 / scale, bw = w + padX * 2, bh = fs + padY * 2;
      if (prio > 1 && placed.some(([x, y, ww, hh]) => x0 < x + ww && x0 + bw > x && y0 < y + hh && y0 + bh > y)) continue;
      placed.push([x0, y0, bw, bh]);
      count++;
      const a = revealOf(n, now) * (prio >= 7 ? 0.6 : 1) * (ip < 1 ? ip : 1);
      ctx.globalAlpha = a;
      ctx.fillStyle = "rgba(8,10,18,0.72)";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x0, y0, bw, bh, 4 / scale);
      else ctx.rect(x0, y0, bw, bh);
      ctx.fill();
      const h = hl.nodes.get(n.id);
      ctx.fillStyle = h?.status === "exposed" ? "#ffd1da" : h?.status === "safe" ? "#c7f9e3" : prio <= 1 ? "#ffffff" : isSvc ? "rgba(226,232,255,0.9)" : "rgba(203,213,225,0.8)";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(n.id, n.x, y0 + padY);
      ctx.globalAlpha = 1;
    }
  }, [data, hl, hover, neighbors, linkByKey, wave, reduced]); // eslint-disable-line react-hooks/exhaustive-deps

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
        nodeVal={(n: any) => (n.kind === "Service" ? 26 : hl.nodes.has(n.id) ? 9 : 2.2)}
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
          const ip = introProgress(performance.now());
          const h = hl.links.get(l.key);
          if (h) return h.status === "exposed" ? `rgba(255,77,109,${0.7 * ip})` : h.status === "path" ? `rgba(167,139,250,${0.9 * ip})` : `rgba(52,211,153,${0.5 * ip})`;
          const a = hover && (l.source?.id === hover || l.target?.id === hover) ? 0.5 : hl.active ? 0.025 : 0.09;
          return `rgba(148,163,184,${a * ip})`;
        }}
        linkWidth={(l: any) => (hl.links.has(l.key) ? 1.6 : hover && (l.source?.id === hover || l.target?.id === hover) ? 1 : 0.5)}
        linkDirectionalParticles={(l: any) => (hl.links.get(l.key)?.status === "path" ? 3 : 0)}
        linkDirectionalParticleWidth={3}
        linkDirectionalParticleSpeed={0.006}
        linkDirectionalParticleColor={() => "#c4b5fd"}
        nodeCanvasObject={(n: any, ctx: CanvasRenderingContext2D, scale: number) => {
          const now = performance.now();
          const h = hl.nodes.get(n.id);
          const isSvc = n.kind === "Service";
          const r = isSvc ? 5 : h ? 3 : 1.5;
          const col = colorOf(n);
          const near = hover === n.id || (hover ? neighbors.get(hover)?.has(n.id) : false);
          const faded = hl.active && !h && !near;
          const reveal = revealOf(n, now);
          if (reveal <= 0.001) return;
          ctx.globalAlpha = reveal * (faded ? (isSvc ? 0.35 : 0.15) : 1);
          if (!isSvc && !h && !near) {
            ctx.fillStyle = col;
            ctx.fillRect(n.x - r, n.y - r, r * 2, r * 2); // cheap path for the ~2,700 background versions
            ctx.globalAlpha = 1;
            return;
          }
          const gs = (h ? 7 : 5) * r * (hover === n.id ? 1.3 : 1);
          ctx.drawImage(glowSprite(col), n.x - gs / 2, n.y - gs / 2, gs, gs);
          ctx.beginPath();
          ctx.arc(n.x, n.y, r * (hover === n.id ? 1.25 : 1), 0, 2 * Math.PI);
          ctx.fillStyle = col;
          ctx.fill();
          if (h && h.status === "exposed" && (h.type === "service" || h.type === "affected") && !reduced) {
            const t = (now % 1800) / 1800;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 2 + t * 10, 0, 2 * Math.PI);
            ctx.strokeStyle = `rgba(255,77,109,${0.5 * (1 - t)})`;
            ctx.lineWidth = 1.2 / Math.max(scale * 0.5, 0.6);
            ctx.stroke();
          }
          if (isSvc) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 1.6, 0, 2 * Math.PI);
            ctx.strokeStyle = hover === n.id ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.22)";
            ctx.lineWidth = 0.6;
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
const easeInOut = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

// Pre-rendered radial glow per color: drawImage is far cheaper than canvas shadowBlur at 60fps.
const glowCache = new Map<string, HTMLCanvasElement>();
function glowSprite(color: string): HTMLCanvasElement {
  let c = glowCache.get(color);
  if (c) return c;
  c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const rgb = toRgb(color);
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, `rgba(${rgb},0.55)`);
  grad.addColorStop(0.35, `rgba(${rgb},0.18)`);
  grad.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  glowCache.set(color, c);
  return c;
}

function toRgb(color: string): string {
  if (color.startsWith("#")) {
    const v = parseInt(color.slice(1), 16);
    return `${(v >> 16) & 255},${(v >> 8) & 255},${v & 255}`;
  }
  const m = color.match(/\d+(\.\d+)?/g);
  return m ? m.slice(0, 3).join(",") : "255,255,255";
}

function tooltip(n: N, h?: { type: string; status?: string; bad?: string }): string {
  const kind = n.kind === "Service" ? `Service · tier ${n.tier ?? "?"} · ${n.team ?? ""}` : "Package version";
  const st = h?.status === "exposed" ? `<div style="color:#ff8fa3;margin-top:4px">exposed${h.bad ? `: ${h.bad}` : ""}</div>`
    : h?.status === "safe" ? `<div style="color:#6ee7b7;margin-top:4px">range excludes the bad version</div>`
    : h?.status === "path" ? `<div style="color:#c4b5fd;margin-top:4px">on the shortest path</div>` : "";
  return `<div style="font-family:var(--font-geist-sans);background:rgba(12,14,22,.92);border:1px solid rgba(255,255,255,.12);padding:8px 10px;border-radius:10px;font-size:12px;color:#e5e7eb;box-shadow:0 10px 30px rgba(0,0,0,.5)"><div style="font-weight:600">${n.id}</div><div style="opacity:.6;font-size:11px">${kind}</div>${st}</div>`;
}
