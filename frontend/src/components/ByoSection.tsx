"use client";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { CalendarClock, CheckCircle2, FileJson, GitFork as Github, Loader2, ScanSearch, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, apiPost } from "@/lib/api";
import type { ByoItem, ByoScan } from "@/lib/types";
import { Card, CardHeader, CopyButton, PathChain, Pill } from "./ui";
import { TracePanel } from "./panels";

const GraphCanvas = dynamic(() => import("./GraphCanvas"), { ssr: false });

const PRESETS = [
  { label: "axios/axios", url: "https://github.com/axios/axios", dev: true },
  { label: "expressjs/express@4.x (runtime only)", url: "https://github.com/expressjs/express/tree/4.x", dev: false },
];

const STEPS = ["Fetching package.json", "Resolving the dependency graph (deps.dev)", "Loading an isolated graph into FalkorDB", "Traversing every chain against the 19 malicious versions"];
const SAMPLE = `{
  "name": "my-app",
  "dependencies": {
    "express": "^5.1.0",
    "eslint": "^9.9.0",
    "sharp": "^0.33.5",
    "ora": "^8.1.0"
  }
}`;

function norm(i: ByoItem) {
  const p = i.path ?? {};
  return {
    via: p.via ?? i.via ?? [],
    requirement: p.requirement ?? i.requirement ?? null,
    reason: p.reason ?? i.reason ?? "",
    hops: p.hops ?? i.hops ?? 0,
  };
}

export default function ByoSection() {
  const [tab, setTab] = useState<"github" | "json">("github");
  const [url, setUrl] = useState("");
  const [json, setJson] = useState(SAMPLE);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(0);
  const [scan, setScan] = useState<ByoScan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [includeDev, setIncludeDev] = useState(true);

  useEffect(() => {
    if (!loading) return;
    setStep(0);
    const t = setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 1100);
    return () => clearInterval(t);
  }, [loading]);

  const submit = async (preset?: { url: string; dev: boolean }) => {
    setErr(null);
    if (!preset && tab === "json") {
      try {
        JSON.parse(json);
      } catch {
        setErr("That is not valid JSON. Paste the contents of a package.json file.");
        return;
      }
    }
    setLoading(true);
    try {
      const ghUrl = (preset?.url ?? url).trim();
      const dev = preset ? preset.dev : includeDev;
      const body = preset || tab === "github" ? { github_url: ghUrl, include_dev: dev } : { package_json: json, include_dev: dev };
      setScan(await apiPost<ByoScan>("/api/byo/scan", body));
    } catch (e) {
      const a = e as ApiError;
      setScan(null);
      setErr(a.status === 404 || a.status === 405 ? "Repo scanning is not enabled on this backend yet." : a.message);
    } finally {
      setLoading(false);
    }
  };

  const rawOv = scan?.overrides ? ("overrides" in scan.overrides ? scan.overrides : { overrides: scan.overrides }) : null;
  const overrides = rawOv && Object.keys((rawOv as { overrides: object }).overrides ?? {}).length ? rawOv : null;
  const hit = (scan?.exposed.length ?? 0) > 0;

  return (
    <section id="byo" className="mt-14">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/30 px-3 py-1 text-[11px] text-white/60"><CalendarClock className="h-3.5 w-3.5 text-amber-300" />Bring your own repo</p>
          <h2 className="text-gradient text-3xl font-semibold tracking-tight md:text-4xl">Would you have been hit on Sept 8, 2025?</h2>
          <p className="mt-2 max-w-2xl text-[14px] text-white/50">Paste a public GitHub repo or a package.json. PatientZero resolves its real dependency graph, loads it into its own FalkorDB graph, and replays the qix compromise against it.</p>
        </div>
      </div>

      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          {([["github", "GitHub URL", <Github key="g" className="h-3.5 w-3.5" />], ["json", "package.json", <FileJson key="j" className="h-3.5 w-3.5" />]] as const).map(([k, label, icon]) => (
            <button key={k} onClick={() => setTab(k)} className={clsx("inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition", tab === k ? "bg-white/10 text-white" : "text-white/50 hover:text-white/80")}>{icon}{label}</button>
          ))}
        </div>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="mt-3 flex flex-col gap-3 md:flex-row md:items-start">
          {tab === "github" ? (
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo" aria-label="GitHub URL"
              className="flex-1 rounded-xl border border-white/10 bg-black/30 px-4 py-3 font-mono text-[13px] text-white placeholder:text-white/25 focus:border-indigo-400/40 focus:outline-none" />
          ) : (
            <textarea value={json} onChange={(e) => setJson(e.target.value)} rows={8} aria-label="package.json"
              className="scroll-thin flex-1 rounded-xl border border-white/10 bg-black/30 px-4 py-3 font-mono text-[12px] text-white/85 focus:border-indigo-400/40 focus:outline-none" />
          )}
          <button type="submit" disabled={loading || (tab === "github" && !url.trim())}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-amber-400 to-rose-500 px-5 py-3 text-sm font-semibold text-black shadow-[0_10px_40px_-10px_rgba(251,191,36,0.6)] transition hover:brightness-110 disabled:opacity-40">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanSearch className="h-4 w-4" />} Scan
          </button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px]">
          <label className="mr-2 inline-flex cursor-pointer items-center gap-2 text-white/55">
            <input type="checkbox" checked={includeDev} onChange={(e) => setIncludeDev(e.target.checked)} className="accent-amber-400" />
            Include devDependencies
          </label>
          <span className="text-white/30">Try:</span>
          {PRESETS.map((p) => (
            <button key={p.label} disabled={loading} onClick={() => { setTab("github"); setUrl(p.url); setIncludeDev(p.dev); submit(p); }}
              className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-[11.5px] text-white/70 transition hover:border-amber-300/30 hover:text-white disabled:opacity-40">
              {p.label}
            </button>
          ))}
        </div>

        <AnimatePresence>
          {loading && (
            <motion.ol initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="mt-4 space-y-1.5">
              {STEPS.map((s, i) => (
                <li key={s} className={clsx("flex items-center gap-2 text-[12.5px]", i < step ? "text-emerald-300/80" : i === step ? "text-white/85" : "text-white/30")}>
                  {i < step ? <CheckCircle2 className="h-3.5 w-3.5" /> : i === step ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <span className="h-3.5 w-3.5 rounded-full border border-white/15" />}
                  {s}
                </li>
              ))}
            </motion.ol>
          )}
        </AnimatePresence>
        {err && <p className="mt-3 rounded-xl border border-rose-400/20 bg-rose-500/[0.06] px-4 py-2.5 text-[13px] text-rose-200/90">{err}</p>}
      </Card>

      {scan && (
        <div className="mt-4 space-y-4">
          <Card className={clsx("p-5", hit && "ring-glow-danger")}>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-start gap-3">
                <span className={clsx("grid h-11 w-11 place-items-center rounded-2xl", hit ? "bg-rose-500/15" : "bg-emerald-500/15")}>
                  {hit ? <ShieldAlert className="h-5 w-5 text-rose-300" /> : <CheckCircle2 className="h-5 w-5 text-emerald-300" />}
                </span>
                <div>
                  <p className="text-xl font-semibold tracking-tight">{hit ? "Yes. A fresh install could have pulled malware." : "No. Every range excluded the malicious versions."}</p>
                  <p className="mt-1 max-w-3xl text-[14px] text-white/60">{scan.summary}</p>
                </div>
              </div>
              <div className="flex gap-2">
                {scan.source && <Pill>{scan.source.label}</Pill>}
                <Pill tone="accent">{scan.scan_id}</Pill>
              </div>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            {scan.graph && scan.graph.nodes.length > 0 && (
              <Card className="relative h-[460px] overflow-hidden">
                <GraphCanvas fleet={null} highlight={scan.graph} leftInset={0} />
                <span className="pointer-events-none absolute left-4 top-4"><Pill>{scan.graph.nodes.length} nodes · {scan.graph.edges.length} edges</Pill></span>
              </Card>
            )}
            <div className="space-y-4">
              <Card>
                <CardHeader icon={<ShieldAlert className="h-4 w-4" />} title={`Exposed (${scan.exposed.length})`} sub="Last-edge range admits the malicious version" right={overrides ? <CopyButton text={JSON.stringify(overrides, null, 2)} label="Copy overrides" /> : undefined} />
                <div className="scroll-thin max-h-72 space-y-2 overflow-y-auto p-4">
                  {scan.exposed.length === 0 && <p className="py-4 text-center text-sm text-white/40">Nothing exposed.</p>}
                  {scan.exposed.map((i, k) => {
                    const p = norm(i);
                    return (
                      <div key={k} className="rounded-xl border border-rose-400/10 bg-rose-500/[0.04] p-3">
                        <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs"><span className="font-mono text-white/85">{i.package}@{i.bad_version}</span>{i.advisory_id && <Pill tone="danger">{i.advisory_id}</Pill>}{i.clean_version && <Pill tone="safe">pin {i.clean_version}</Pill>}</div>
                        <PathChain via={p.via} requirement={p.requirement} bad={i.bad_version} exposed compact />
                      </div>
                    );
                  })}
                </div>
              </Card>
              <Card>
                <CardHeader icon={<CheckCircle2 className="h-4 w-4" />} title={`Not exposed, with reason (${scan.not_exposed.length})`} />
                <div className="scroll-thin max-h-56 space-y-2 overflow-y-auto p-4">
                  {scan.not_exposed.length === 0 && <p className="py-4 text-center text-sm text-white/40">No near misses.</p>}
                  {scan.not_exposed.map((i, k) => {
                    const p = norm(i);
                    return (
                      <div key={k} className="rounded-xl border border-emerald-400/10 bg-emerald-500/[0.03] p-3">
                        <PathChain via={p.via} requirement={p.requirement} bad={i.bad_version} exposed={false} compact />
                        <p className="mt-1 text-[11.5px] text-emerald-200/70">{p.reason}</p>
                      </div>
                    );
                  })}
                </div>
              </Card>
            </div>
          </div>
          {overrides && (
            <Card className="p-5">
              <div className="mb-2 flex items-center justify-between"><span className="text-[11px] font-medium uppercase tracking-wider text-white/40">package.json overrides</span><CopyButton text={JSON.stringify(overrides, null, 2)} /></div>
              <pre className="scroll-thin overflow-x-auto rounded-xl bg-black/40 p-3 font-mono text-[12px] text-emerald-100/90">{JSON.stringify(overrides, null, 2)}</pre>
            </Card>
          )}
          {scan.trace && scan.trace.length > 0 && <TracePanel trace={scan.trace} loading={false} />}
        </div>
      )}
    </section>
  );
}
