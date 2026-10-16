"use client";

import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { ArrowUpRight, Gauge, History, Loader2, Radar } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, ApiError, apiGet, apiPost, withTenant } from "@/lib/api";
import type { BenchLast, BenchStats, TenantInfoLite, WatchEvent, WatchFeed, WatchPoll, WatchReplay } from "./watch-types";
import { Card, Segmented, Skeleton } from "./ui";

const EASE = [0.22, 1, 0.36, 1] as const;
const REPLAY_TARGET = "debug@4.4.2";

const clock = (s: string | number) =>
  new Date(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
const stamp = (s: string) =>
  new Date(s).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`);
const nf = new Intl.NumberFormat("en-US");

function ago(ts: string, now: number) {
  const s = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function VerdictChip({ e }: { e: Pick<WatchEvent, "verdict" | "exposed_count" | "fleet_size"> }) {
  if (e.verdict === "hit")
    return <span className="inline-flex items-center gap-1.5 rounded-md bg-signal-soft px-1.5 py-0.5 text-[12px] font-medium text-signal-ink tabular"><span className="h-1.5 w-1.5 rounded-full bg-signal" />Hit {e.exposed_count} of {e.fleet_size}</span>;
  if (e.verdict === "reachable_not_admitted")
    return <span className="inline-flex items-center gap-1.5 rounded-md bg-subtle px-1.5 py-0.5 text-[12px] font-medium text-ink-2"><span className="h-1.5 w-1.5 rounded-full border border-ink-3" />Reached, range excludes</span>;
  return <span className="inline-flex items-center gap-1.5 rounded-md bg-safe-soft px-1.5 py-0.5 text-[12px] font-medium text-safe"><span className="h-1.5 w-1.5 rounded-full bg-safe" />Not hit</span>;
}

/** Live OSV watch: real MAL advisories from the OSV npm index, checked against every tenant fleet. */
export function LiveWatch({ tenant, tenants }: { tenant: string; tenants: TenantInfoLite[] }) {
  const [feeds, setFeeds] = useState<Record<string, WatchFeed>>({});
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState<"connecting" | "open" | "closed">("connecting");
  const [tab, setTab] = useState<"real" | "replay">("real");
  const [replay, setReplay] = useState<WatchReplay | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [replayErr, setReplayErr] = useState<string | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());
  // The stream only runs while the panel is near the viewport and the tab is visible: one less open connection per idle visitor.
  const rootRef = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === "undefined") { setNear(true); return; }
    const io = new IntersectionObserver(([e]) => setNear(e.isIntersecting), { rootMargin: "600px 0px" });
    io.observe(el);
    const vis = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", vis);
    return () => { io.disconnect(); document.removeEventListener("visibilitychange", vis); };
  }, []);
  const streamOn = near && visible;
  const ids = useMemo(() => (tenants.length ? tenants.map((t) => t.id) : [tenant]), [tenants, tenant]);
  const company = (id: string) => tenants.find((t) => t.id === id)?.company ?? id[0].toUpperCase() + id.slice(1);

  const load = useCallback(async () => {
    try {
      const all = await Promise.all(ids.map((id) => apiGet<WatchFeed>("/api/watch/feed?limit=200", id)));
      setFeeds(Object.fromEntries(all.map((f) => [f.tenant, f])));
      setErr(null);
    } catch (e) {
      setErr((e as ApiError).status === 404 ? "The watch is not enabled on this server." : (e as Error).message);
    }
  }, [ids]);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(t); }, []);

  // One SSE stream for the tenant on screen. New events land on top; polls refresh the verdicts for every tenant.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    if (!streamOn) { setLive("closed"); return; }
    setLive("connecting");
    const es = new EventSource(`${API_URL}${withTenant("/api/watch/stream", tenant)}`);
    es.addEventListener("hello", () => setLive("open"));
    es.onopen = () => setLive("open");
    es.onerror = () => setLive(es.readyState === EventSource.CLOSED ? "closed" : "connecting");
    es.addEventListener("watch", (m) => {
      try {
        const e = JSON.parse((m as MessageEvent).data) as WatchEvent;
        setFeeds((f) => {
          const cur = f[e.tenant];
          if (!cur || cur.events.some((x) => x.id === e.id)) return f;
          return { ...f, [e.tenant]: { ...cur, events: [e, ...cur.events] } };
        });
        setFresh((s) => new Set(s).add(e.id));
      } catch { /* malformed frame */ }
    });
    es.addEventListener("poll", () => { loadRef.current(); });
    loadRef.current(); // catch up on anything that landed while the stream was off
    return () => es.close();
  }, [tenant, streamOn]);

  const doReplay = async () => {
    setReplaying(true);
    setReplayErr(null);
    try {
      const r = await apiPost<WatchReplay>("/api/watch/replay", { advisory: REPLAY_TARGET });
      setReplay(r);
      setTab("replay");
      load();
    } catch (e) {
      const ae = e as ApiError;
      setReplayErr(ae.status === 429 ? "Replays are limited to 6 a minute. Try again shortly." : ae.message);
    } finally {
      setReplaying(false);
    }
  };

  const feed = feeds[tenant];
  const real = useMemo(() => (feed?.events ?? []).filter((e) => !e.replay), [feed]);
  const replays = useMemo(() => (feed?.events ?? []).filter((e) => e.replay), [feed]);
  const lastPoll: WatchPoll | null = feed?.last_poll ?? null;
  // The newest batch of real advisories: from the poll summary when it is still in the list, else from its events.
  const lastChecked = useMemo(() => {
    const p = feed?.polls.find((x) => x.checked > 0 && !x.replay && x.source !== "replay");
    if (p) return { n: p.checked, ms: p.ms, ts: p.ts, what: "" };
    if (!real.length) return null;
    const batch = real.filter((e) => e.poll_id === real[0].poll_id);
    return { n: batch.length, ms: batch.reduce((a, e) => a + (e.check_ms ?? 0), 0), ts: batch[batch.length - 1].ts, what: " of graph checks" };
  }, [feed, real]);

  return (
    <div ref={rootRef}>
    {err ? <Card className="p-6"><p className="text-[13.5px] text-ink-3">{err}</p></Card>
    : !feed ? <Card className="p-6"><Skeleton className="h-40" /></Card> : (
    <Card className="overflow-hidden">
      <div data-testid="live-watch" className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* Left: status, verdicts, feed */}
        <div className="min-w-0 border-line lg:border-r">
          <div className="border-b border-line px-6 py-5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-ink-3">
              <span className="inline-flex items-center gap-1.5 font-medium text-ink-2" data-testid="watch-live" data-state={live}>
                <span className="relative grid h-2 w-2 place-items-center">
                  {live === "open" && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-safe/50" />}
                  <span className={clsx("relative h-2 w-2 rounded-full", live === "open" ? "bg-safe" : live === "connecting" ? "bg-ink-4" : "bg-risk")} />
                </span>
                {live === "open" ? "Listening" : live === "connecting" ? "Connecting" : streamOn ? "Stream closed" : "Paused"}
              </span>
              <span className="h-3 w-px bg-line" />
              <span className="inline-flex items-center gap-1.5"><Radar className="h-3.5 w-3.5" />OSV npm index, every {Math.round((feed.loop?.interval_s ?? 600) / 60)} min</span>
              {lastPoll && <><span className="h-3 w-px bg-line" /><span data-testid="watch-last-poll" title={lastPoll.note}>Last poll {clock(lastPoll.ts)} ({ago(lastPoll.ts, now)}), {fmtMs(lastPoll.ms)}</span></>}
              {feed.loop?.enabled && feed.loop.next_at && <><span className="h-3 w-px bg-line" /><span>Next {clock(feed.loop.next_at * 1000)}</span></>}
              {feed.running && <span className="inline-flex items-center gap-1 text-ink-2"><Loader2 className="h-3 w-3 animate-spin" />Polling</span>}
            </div>
            <p className="mt-3 text-[20px] font-semibold leading-snug tracking-[-0.015em] text-ink" data-testid="watch-headline">
              {lastChecked
                ? <>Checked {nf.format(lastChecked.n)} new npm malware advisories from OSV in {fmtMs(lastChecked.ms)}{lastChecked.what}<span className="font-normal text-ink-3">, {stamp(lastChecked.ts)}</span></>
                : <>Watching OSV for new npm malware advisories.</>}
            </p>
            {lastPoll && lastPoll.checked === 0 && <p className="mt-1 text-[13px] text-ink-3">{lastPoll.note ?? "No new advisories since the previous poll."}</p>}
            <div className="mt-4 grid gap-2 sm:grid-cols-2" data-testid="watch-verdicts">
              {ids.map((id) => {
                const evs = (feeds[id]?.events ?? []).filter((e) => !e.replay);
                const hits = evs.filter((e) => e.verdict === "hit");
                const exposed = new Set(hits.flatMap((e) => e.exposed_services));
                return (
                  <div key={id} data-tenant={id} className={clsx("flex items-center justify-between gap-3 rounded-lg border px-3.5 py-2.5", id === tenant ? "border-line-strong bg-surface" : "border-line bg-subtle/60")}>
                    <div className="min-w-0">
                      <p className="text-[13px] font-medium text-ink">{company(id)}<span className="ml-1.5 font-mono text-[11.5px] font-normal text-ink-3">{feeds[id]?.graph}</span></p>
                      <p className="text-[12px] text-ink-3 tabular">{evs.length} real advisor{evs.length === 1 ? "y" : "ies"} checked</p>
                    </div>
                    {hits.length
                      ? <span className="shrink-0 rounded-md bg-signal-soft px-2 py-1 text-[12.5px] font-medium text-signal-ink tabular">Hit, {exposed.size} service{exposed.size === 1 ? "" : "s"}</span>
                      : <span className="shrink-0 rounded-md bg-safe-soft px-2 py-1 text-[12.5px] font-medium text-safe">Not hit</span>}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 px-6 pb-2 pt-4">
            <p className="text-[13px] font-medium text-ink">{company(tenant)} feed</p>
            <Segmented size="sm" value={tab} onChange={setTab} options={[
              { value: "real", label: `Real advisories ${real.length}` },
              { value: "replay", label: `Replays ${replays.length}` },
            ]} />
          </div>
          <ul className="scroll-thin max-h-[380px] divide-y divide-line overflow-y-auto" data-testid="watch-feed">
            {(tab === "real" ? real : replays).slice(0, 60).map((e) => (
              <motion.li key={e.id} layout="position" initial={fresh.has(e.id) ? { opacity: 0, y: -6, backgroundColor: "rgba(229,72,77,0.06)" } : false}
                animate={{ opacity: 1, y: 0, backgroundColor: "rgba(255,255,255,0)" }} transition={{ duration: 0.6, ease: EASE }}
                className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-3 px-6 py-2.5 text-[13px]" data-verdict={e.verdict} data-replay={e.replay}>
                <span className="font-mono text-[12px] text-ink-3 tabular" title={stamp(e.ts)}>{clock(e.ts).replace(/\s?[A-Z]{2,4}$/, "")}</span>
                <div className="min-w-0">
                  <p className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate font-mono text-ink">{e.package}<span className="text-ink-3">@{e.all_versions ? "*" : e.bad_versions.slice(0, 2).join(", ")}{!e.all_versions && e.bad_versions.length > 2 ? ` +${e.bad_versions.length - 2}` : ""}</span></span>
                    {e.replay && <span className="shrink-0 rounded border border-line-strong px-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-2">Replay</span>}
                  </p>
                  <p className="truncate text-[12px] text-ink-3">
                    {e.url ? <a href={e.url} target="_blank" rel="noreferrer" className="font-mono hover:text-ink">{e.advisory}</a> : <span className="font-mono">{e.advisory}</span>}
                    {e.reason && <> · {e.reason}</>}
                  </p>
                </div>
                <span className="flex items-center gap-3">
                  {typeof e.check_ms === "number" && <span className="hidden font-mono text-[11.5px] text-ink-4 tabular sm:inline">{fmtMs(e.check_ms)}</span>}
                  <VerdictChip e={e} />
                </span>
              </motion.li>
            ))}
            {(tab === "real" ? real : replays).length === 0 && (
              <li className="px-6 py-10 text-center text-[13px] text-ink-3">{tab === "real" ? "No new advisories have arrived since the watch started." : "No replays yet. Replay a past advisory to see a hit."}</li>
            )}
          </ul>
        </div>

        {/* Right: replay */}
        <div className="flex flex-col gap-4 bg-subtle/50 px-6 py-5">
          <div>
            <p className="text-[13px] font-medium text-ink">Replay a past advisory</p>
            <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">
              The real OSV record for <span className="font-mono text-ink">{REPLAY_TARGET}</span> (Sept 8, 2025) goes through the same pipeline for every tenant.
              It is labelled as a replay and never counted as a new alert.
            </p>
          </div>
          <button onClick={doReplay} disabled={replaying} data-testid="watch-replay"
            className="press inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-surface px-3.5 text-[13.5px] font-medium text-ink ring-1 ring-inset ring-line-strong transition-colors hover:bg-paper disabled:opacity-50">
            {replaying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <History className="h-3.5 w-3.5" />} Replay {REPLAY_TARGET}
          </button>
          {replayErr && <p className="text-[12.5px] text-signal-ink">{replayErr}</p>}
          <AnimatePresence mode="wait">
            {replay && (
              <motion.div key={replay.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.3, ease: EASE }}
                data-testid="watch-replay-result" className="space-y-2.5">
                <p className="flex items-center gap-2 text-[12px] text-ink-3">
                  <span className="rounded border border-line-strong bg-surface px-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-2">Replay</span>
                  <span className="font-mono">{replay.advisory}</span>
                  <span className="ml-auto tabular">{fmtMs(replay.ms)}</span>
                </p>
                {replay.events.map((e) => (
                  <div key={e.tenant} data-tenant={e.tenant} className="rounded-lg border border-line bg-surface px-3.5 py-3">
                    <div className="flex items-baseline justify-between gap-3">
                      <p className="text-[13px] font-medium text-ink">{company(e.tenant)}</p>
                      <p className="text-[13px] tabular">
                        {e.verdict === "hit"
                          ? <><span className="text-[17px] font-semibold text-signal">{e.exposed_count}</span><span className="text-ink-3"> of {e.fleet_size} exposed</span></>
                          : <span className="text-safe">Not hit</span>}
                      </p>
                    </div>
                    {e.exposed_services.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {e.exposed_services.slice(0, 8).map((s) => <span key={s} className="rounded border border-line px-1.5 py-px font-mono text-[11px] text-ink-2">{s}</span>)}
                        {e.exposed_services.length > 8 && <span className="px-1 text-[11px] text-ink-3">+{e.exposed_services.length - 8}</span>}
                      </div>
                    )}
                  </div>
                ))}
                <p className="text-[12px] leading-relaxed text-ink-3">{replay.note}</p>
              </motion.div>
            )}
          </AnimatePresence>
          {!replay && (
            <div className="rounded-lg border border-dashed border-line-strong px-3.5 py-4 text-[12.5px] leading-relaxed text-ink-3">
              A real new advisory that hits a fleet shows up in the feed within one poll, with the services it reaches.
            </div>
          )}
          <a href="https://osv.dev/list?ecosystem=npm&q=MAL-" target="_blank" rel="noreferrer" className="mt-auto inline-flex items-center gap-1 text-[12.5px] text-ink-3 hover:text-ink">
            Source: OSV.dev npm malware <ArrowUpRight className="h-3 w-3" />
          </a>
        </div>
      </div>
    </Card>
    )}
    </div>
  );
}

/** The last stored concurrency benchmark. A saved result, labelled with when and where it ran. */
export function BenchPanel() {
  const [d, setD] = useState<BenchLast | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    apiGet<BenchLast>("/api/bench/last").then(setD, (e) => setErr((e as ApiError).status === 404 ? "No benchmark endpoint on this server." : (e as Error).message));
  }, []);
  if (err) return <Card className="p-6"><p className="text-[13.5px] text-ink-3">{err}</p></Card>;
  if (!d) return <Card className="p-6"><Skeleton className="h-32" /></Card>;
  const b = d.last;
  if (!b) return <Card className="p-6" ><p className="text-[13.5px] text-ink-3" data-testid="bench">No benchmark has been saved on this deploy yet.</p></Card>;
  const o = b.overall;
  const lat: [string, number][] = [["p50", o.p50_ms], ["p95", o.p95_ms], ["p99", o.p99_ms], ["max", o.max_ms]];
  const top = Math.max(...lat.map((x) => x[1]), 1);
  const mix = b.mix ? Object.entries(b.mix) : [];
  const writePct = mix.filter(([k]) => k.startsWith("write")).reduce((a, [, v]) => a + v, 0);
  const where = b.label === "local" ? "a development machine" : b.label === "server" ? "the demo server" : b.label;
  return (
    <Card className="overflow-hidden">
      <div data-testid="bench" data-label={b.label} className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <div className="border-line px-6 py-5 lg:border-r">
          <p className="flex items-center gap-2 text-[12.5px] text-ink-3"><Gauge className="h-3.5 w-3.5" />Last saved run · {stamp(b.at)}</p>
          <p className="mt-3 flex items-baseline gap-2">
            <span className="text-[40px] font-semibold leading-none tracking-[-0.03em] text-ink tabular" data-testid="bench-throughput">{nf.format(Math.round(o.throughput_ops_s))}</span>
            <span className="text-[14px] text-ink-2">queries per second</span>
          </p>
          <p className="mt-2 text-[13px] text-ink-2 tabular">
            {nf.format(o.ops)} queries, <span className={o.errors ? "font-medium text-signal-ink" : "font-medium text-safe"}>{nf.format(o.errors)} error{o.errors === 1 ? "" : "s"}</span>
          </p>
          <div className="mt-5 space-y-2" data-testid="bench-latency">
            {lat.map(([k, v], i) => (
              <div key={k} className="grid grid-cols-[34px_minmax(0,1fr)_64px] items-center gap-3 text-[12.5px]">
                <span className="font-mono text-ink-3">{k}</span>
                <span className="h-1.5 overflow-hidden rounded-full bg-subtle">
                  <motion.span className={clsx("block h-full rounded-full", k === "max" ? "bg-ink-4" : "bg-ink")} initial={{ width: 0 }} whileInView={{ width: `${(v / top) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.6, delay: i * 0.06, ease: EASE }} />
                </span>
                <span className="text-right font-mono text-ink tabular">{v.toFixed(1)} ms</span>
              </div>
            ))}
          </div>
        </div>
        <div className="px-6 py-5">
          <table className="w-full text-[12.5px] tabular">
            <thead><tr className="text-left text-ink-3"><th className="pb-2 font-normal">Workload</th><th className="pb-2 text-right font-normal">ops/s</th><th className="pb-2 text-right font-normal">p50</th><th className="pb-2 text-right font-normal">p95</th><th className="pb-2 text-right font-normal">p99</th><th className="pb-2 text-right font-normal">errors</th></tr></thead>
            <tbody className="divide-y divide-line border-t border-line">
              {([["Reads", b.reads], ["Writes", b.writes], ...Object.entries(b.by_op ?? {}).map(([k, v]) => [k.replace(/_/g, " "), v] as [string, BenchStats])] as [string, BenchStats | undefined][])
                .filter(([, v]) => v).map(([k, v], i) => (
                  <tr key={k} className={i < 2 ? "text-ink" : "text-ink-2"}>
                    <td className={clsx("py-1.5", i >= 2 && "pl-3 font-mono text-[11.5px]")}>{k}</td>
                    <td className="py-1.5 text-right font-mono">{nf.format(Math.round(v!.throughput_ops_s))}</td>
                    <td className="py-1.5 text-right font-mono">{v!.p50_ms.toFixed(1)}</td>
                    <td className="py-1.5 text-right font-mono">{v!.p95_ms.toFixed(1)}</td>
                    <td className="py-1.5 text-right font-mono">{v!.p99_ms.toFixed(1)}</td>
                    <td className={clsx("py-1.5 text-right font-mono", v!.errors ? "text-signal-ink" : "text-ink-3")}>{v!.errors}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="mt-4 text-[12.5px] leading-relaxed text-ink-2" data-testid="bench-note">
            {b.clients} concurrent clients{b.procs ? ` in ${b.procs} processes` : ""} for {b.duration_s} s on {where} (label <span className="font-mono">{b.label}</span>),
            {writePct ? ` ${100 - writePct}% reads and ${writePct}% writes,` : ""} reading <span className="font-mono">{b.read_graph ?? "the fleet graph"}</span>
            {b.falkordb?.redis_version ? ` on FalkorDB (Redis ${b.falkordb.redis_version})` : ""}.
            {b.scratch_deleted ? " Writes went to a scratch graph that was deleted afterwards." : ""}
            {b.retry_busy ? " Busy replies were retried, as the app does." : " Busy replies count as errors."}
            {" "}This is a stored result from that run, not measured while this page loads.
          </p>
          {d.history.length > 1 && (
            <p className="mt-3 text-[12px] text-ink-3">
              Earlier runs: {d.history.slice(1, 4).map((h) => `${h.label} ${nf.format(Math.round(h.overall.throughput_ops_s))}/s, p95 ${h.overall.p95_ms.toFixed(0)} ms (${h.clients} clients)`).join("; ")}
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}


