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

export default function GraphCanvas({ fleet, highlight, onNodeClick, leftInset }: Props) {
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
    const overlay = isStandalone || leftPad ? 0 : 260;
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
  }, [hl, data, size.w, size.h, leftInset, isStandalone]);

  useEffect(() => {
    const t = setTimeout(() => fit(), hl.active ? 150 : 1800);
    return () => clearTimeout(t);
  }, [fit, hl.active]);

  const colorOf = (n: N): string => {
    const h = hl.nodes.get(n.id);
    if (h) {
      if (h.status === "path") return n.kind === "Service" ? C.service : h.type === "affected" ? C.path : C.path;
      if (h.type === "affected") return h.status === "exposed" ? C.exposed : C.safe;
      if (h.type === "service") return h.status === "exposed" ? C.exposed : C.safe;
      return h.status === "exposed" ? C.chain : "rgba(52,211,153,0.8)";
    }
    return n.kind === "Service" ? C.service : C.dim;
  };

  return (
    <div ref={wrap} className="absolute inset-0">
      <ForceGraph2D
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        warmupTicks={40}
        cooldownTicks={250}
        onEngineStop={() => isStandalone && fit(600)}
        autoPauseRedraw={false}
        enableNodeDrag
        minZoom={0.3}
        maxZoom={12}
        nodeRelSize={1}
        nodeVal={(n: any) => (n.kind === "Service" ? 26 : hl.nodes.has(n.id) ? 9 : 2.2)}
        nodeLabel={(n: any) => tooltip(n, hl.nodes.get(n.id))}
        onNodeHover={(n: any) => setHover(n?.id ?? null)}
        onNodeClick={(n: any) => {
          fg.current?.centerAt(n.x, n.y, 700);
          fg.current?.zoom(4, 700);
          onNodeClick?.(n.id);
        }}
        linkColor={(l: any) => {
          const h = hl.links.get(l.key);
          if (h) return h.status === "exposed" ? "rgba(255,77,109,0.85)" : h.status === "path" ? "rgba(167,139,250,0.9)" : "rgba(52,211,153,0.55)";
          return hl.active ? "rgba(255,255,255,0.025)" : "rgba(148,163,184,0.09)";
        }}
        linkWidth={(l: any) => (hl.links.has(l.key) ? 1.8 : 0.5)}
        linkDirectionalParticles={(l: any) => {
          const h = hl.links.get(l.key);
          return h && h.status !== "safe" ? 3 : 0;
        }}
        linkDirectionalParticleWidth={(l: any) => (hl.links.get(l.key)?.status === "path" ? 3 : 2.6)}
        linkDirectionalParticleSpeed={0.006}
        linkDirectionalParticleColor={(l: any) => (hl.links.get(l.key)?.status === "path" ? "#c4b5fd" : "#ff8fa3")}
        nodeCanvasObject={(n: any, ctx: CanvasRenderingContext2D, scale: number) => {
          const h = hl.nodes.get(n.id);
          const isSvc = n.kind === "Service";
          const r = isSvc ? 5 : h ? 3 : 1.5;
          const col = colorOf(n);
          const faded = hl.active && !h && !(isSvc && hover === n.id);
          ctx.globalAlpha = faded ? (isSvc ? 0.35 : 0.15) : 1;
          // glow
          if (h || isSvc) {
            ctx.shadowColor = col;
            ctx.shadowBlur = h ? 18 : 10;
          }
          ctx.beginPath();
          ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
          ctx.fillStyle = col;
          ctx.fill();
          ctx.shadowBlur = 0;
          // pulse ring on exposed services and affected packages
          if (h && h.status === "exposed" && (h.type === "service" || h.type === "affected")) {
            const t = (performance.now() % 1600) / 1600;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 2 + t * 10, 0, 2 * Math.PI);
            ctx.strokeStyle = `rgba(255,77,109,${0.55 * (1 - t)})`;
            ctx.lineWidth = 1.2 / Math.max(scale * 0.5, 0.6);
            ctx.stroke();
          }
          if (isSvc) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 1.6, 0, 2 * Math.PI);
            ctx.strokeStyle = "rgba(255,255,255,0.25)";
            ctx.lineWidth = 0.6;
            ctx.stroke();
          }
          // labels: services always, highlighted nodes when highlighting, others when zoomed in
          const showLabel = isSvc || h || hover === n.id || (!hl.active && scale > 3.2);
          if (showLabel) {
            const fs = Math.max((isSvc ? 11 : 9.5) / scale, 1.6);
            ctx.font = `${isSvc ? 600 : 500} ${fs}px var(--font-geist-sans), system-ui, sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillStyle = h ? "rgba(255,255,255,0.95)" : isSvc ? "rgba(226,232,255,0.85)" : "rgba(203,213,225,0.7)";
            ctx.fillText(n.id, n.x, n.y + r + 2 / scale + 1);
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

function tooltip(n: N, h?: { type: string; status?: string; bad?: string }): string {
  const kind = n.kind === "Service" ? `Service · tier ${n.tier ?? "?"} · ${n.team ?? ""}` : "Package version";
  const st = h?.status === "exposed" ? `<div style="color:#ff8fa3;margin-top:4px">exposed${h.bad ? `: ${h.bad}` : ""}</div>`
    : h?.status === "safe" ? `<div style="color:#6ee7b7;margin-top:4px">range excludes the bad version</div>`
    : h?.status === "path" ? `<div style="color:#c4b5fd;margin-top:4px">on the shortest path</div>` : "";
  return `<div style="font-family:var(--font-geist-sans);background:rgba(12,14,22,.92);border:1px solid rgba(255,255,255,.12);padding:8px 10px;border-radius:10px;font-size:12px;color:#e5e7eb;box-shadow:0 10px 30px rgba(0,0,0,.5)"><div style="font-weight:600">${n.id}</div><div style="opacity:.6;font-size:11px">${kind}</div>${st}</div>`;
}
