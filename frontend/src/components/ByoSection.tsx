"use client";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { CheckCircle2, FileJson, GitFork as Github, Loader2, ScanSearch } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, apiPost } from "@/lib/api";
import type { ByoItem, ByoScan } from "@/lib/types";
import { Button, Card, CardHeader, CopyButton, PathChain, Segmented, ShowMore } from "./ui";
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

/** Drop the leading "Yes:"/"No:" (the headline says it) and capitalize, unless the sentence starts with a host or path. */
function tidy(summary: string) {
  const t = summary.replace(/^(Yes|No)[:.]\s*/, "");
  return /^[^\s]*[./]/.test(t) ? t : t.charAt(0).toUpperCase() + t.slice(1);
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
    <section id="byo" className="scroll-mt-20 pt-24">
      <p className="eyebrow">Bring your own repo</p>
      <h2 className="mt-2 text-[28px] font-semibold tracking-[-0.02em] text-ink">Would you have been hit on Sept 8, 2025?</h2>
      <p className="mt-2 max-w-[640px] text-[15px] leading-relaxed text-ink-2">Paste a public GitHub repo or a package.json. PatientZero resolves its real dependency graph, loads it into a separate FalkorDB graph, and replays the qix compromise against it.</p>

      <Card className="mt-8 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Segmented value={tab} onChange={setTab} options={[
            { value: "github", label: <span className="inline-flex items-center gap-1.5"><Github className="h-3.5 w-3.5" />GitHub URL</span> },
            { value: "json", label: <span className="inline-flex items-center gap-1.5"><FileJson className="h-3.5 w-3.5" />package.json</span> },
          ]} />
          <label className="inline-flex cursor-pointer select-none items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" checked={includeDev} onChange={(e) => setIncludeDev(e.target.checked)} className="h-3.5 w-3.5 accent-ink" />
            Include devDependencies
          </label>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="mt-4 flex flex-col gap-3 md:flex-row md:items-start">
          {tab === "github" ? (
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo" aria-label="GitHub URL"
              className="h-11 flex-1 rounded-lg border border-line bg-surface px-3.5 font-mono text-[13.5px] text-ink placeholder:text-ink-3 transition-colors focus:border-ink-3 focus:outline-none" />
          ) : (
            <textarea value={json} onChange={(e) => setJson(e.target.value)} rows={8} aria-label="package.json"
              className="scroll-thin flex-1 rounded-lg border border-line bg-surface px-3.5 py-3 font-mono text-[12.5px] text-ink transition-colors focus:border-ink-3 focus:outline-none" />
          )}
          <Button type="submit" variant="primary" disabled={loading || (tab === "github" && !url.trim())} className="h-11 px-5 text-[14px]">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanSearch className="h-4 w-4" />} Scan
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
          <span className="text-ink-3">Examples</span>
          {PRESETS.map((p) => (
            <button key={p.label} disabled={loading} onClick={() => { setTab("github"); setUrl(p.url); setIncludeDev(p.dev); submit(p); }}
              className="press font-mono text-[12.5px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink hover:decoration-ink disabled:opacity-40">
              {p.label}
            </button>
          ))}
        </div>

        <AnimatePresence>
          {loading && (
            <motion.ol initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.2 }} className="mt-5 space-y-2 border-t border-line pt-4">
              {STEPS.map((s, i) => (
                <li key={s} className={clsx("flex items-center gap-2.5 text-[13px] transition-colors", i < step ? "text-ink-2" : i === step ? "text-ink" : "text-ink-3")}>
                  {i < step ? <CheckCircle2 className="h-4 w-4 text-safe" /> : i === step ? <Loader2 className="h-4 w-4 animate-spin" /> : <span className="h-4 w-4 rounded-full border border-line-strong" />}
                  {s}
                </li>
              ))}
            </motion.ol>
          )}
        </AnimatePresence>
        {err && <p className="mt-4 rounded-lg border border-signal/30 bg-signal-soft px-4 py-2.5 text-[13.5px] text-signal-ink">{err}</p>}
      </Card>

      {scan && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className="mt-10">
          <p className="flex flex-wrap items-center gap-x-2 text-[13px] text-ink-3">
            <span className="font-medium text-ink-2">Scan</span>
            {scan.source && <><span className="text-line-strong">/</span><span className="font-mono">{scan.source.label}</span></>}
            <span className="text-line-strong">/</span><span className="font-mono">{scan.scan_id}</span>
          </p>
          <h3 className="mt-3 flex items-center gap-3 text-[26px] font-semibold tracking-[-0.02em] text-ink">
            <span className={clsx("h-2.5 w-2.5 shrink-0 rounded-full", hit ? "bg-signal" : "bg-safe")} />
            {hit ? "Yes. A fresh install could have pulled malware." : "No. Every range excluded the malicious versions."}
          </h3>
          <p className="mt-2 max-w-[820px] text-[15px] leading-relaxed text-ink-2">{tidy(scan.summary)}</p>

          <div className="mt-8 grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            {scan.graph && scan.graph.nodes.length > 0 && (
              <Card className="relative h-[480px] overflow-hidden">
                <GraphCanvas fleet={null} highlight={scan.graph} leftInset={0} />
                <span className="pointer-events-none absolute left-4 top-3 text-[12px] text-ink-3 tabular">{scan.graph.nodes.length} nodes · {scan.graph.edges.length} edges</span>
              </Card>
            )}
            <div className="space-y-4">
              <Card>
                <CardHeader title={`Exposed`} sub="The range on the last edge admits the malicious version." right={<span className={clsx("text-[13px] font-semibold tabular", scan.exposed.length ? "text-signal-ink" : "text-ink-3")}>{scan.exposed.length}</span>} />
                <div className="divide-y divide-line">
                  {scan.exposed.length === 0 && <p className="px-5 py-4 text-[13px] text-ink-3">Nothing exposed.</p>}
                  <ShowMore key={scan.scan_id} limit={4} noun="exposed versions">
                  {scan.exposed.map((i, k) => {
                    const p = norm(i);
                    return (
                      <div key={k} className="px-5 py-3">
                        <div className="mb-1.5 flex flex-wrap items-baseline gap-x-3 text-[12.5px]">
                          <span className="font-mono font-medium text-ink">{i.package}@{i.bad_version}</span>
                          {i.clean_version && <span className="text-ink-2">pin {i.clean_version}</span>}
                          {i.advisory_id && <span className="font-mono text-ink-3">{i.advisory_id}</span>}
                        </div>
                        <PathChain via={p.via} requirement={p.requirement} bad={i.bad_version} exposed compact />
                      </div>
                    );
                  })}
                  </ShowMore>
                </div>
              </Card>
              <Card>
                <CardHeader title="Excluded by range" sub="Reaches an affected package, but the range rules the bad version out." right={<span className="text-[13px] font-semibold text-ink-2 tabular">{scan.not_exposed.length}</span>} />
                <div className="divide-y divide-line">
                  {scan.not_exposed.length === 0 && <p className="px-5 py-4 text-[13px] text-ink-3">No near misses.</p>}
                  <ShowMore key={scan.scan_id} limit={3} noun="near misses">
                  {scan.not_exposed.map((i, k) => {
                    const p = norm(i);
                    return (
                      <div key={k} className="px-5 py-3">
                        <PathChain via={p.via} requirement={p.requirement} bad={i.bad_version} exposed={false} compact />
                        <p className="mt-1 text-[12.5px] text-ink-3">{p.reason}</p>
                      </div>
                    );
                  })}
                  </ShowMore>
                </div>
              </Card>
            </div>
          </div>
          {overrides && (
            <Card className="mt-4 p-5">
              <div className="mb-2 flex items-center justify-between"><span className="text-[12.5px] font-medium text-ink-2">package.json overrides</span><CopyButton text={JSON.stringify(overrides, null, 2)} /></div>
              <pre className="scroll-thin overflow-x-auto rounded-lg border border-line bg-subtle p-3.5 font-mono text-[12px] text-ink">{JSON.stringify(overrides, null, 2)}</pre>
            </Card>
          )}
          {scan.trace && scan.trace.length > 0 && <div className="mt-4"><TracePanel trace={scan.trace} loading={false} /></div>}
        </motion.div>
      )}
    </section>
  );
}
