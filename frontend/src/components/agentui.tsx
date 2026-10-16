"use client";
import { motion } from "framer-motion";
import { Bot, BookCheck, CheckCircle2, Cpu, Loader2, ShieldQuestion, Sparkles } from "lucide-react";
import { useState } from "react";
import { apiPost } from "@/lib/api";
import type { AskResponse, Playbook, SummaryResponse } from "@/lib/types";
import { Card, CardHeader, CopyButton, Pill, Skeleton } from "./ui";

/** Text with any unverified graph names highlighted (they were not in the tool results). */
export function FlaggedText({ text, unverified }: { text: string; unverified: string[] }) {
  if (!unverified.length) return <>{text}</>;
  const esc = unverified.map((u) => u.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
  const re = new RegExp(`(${esc.join("|")})`, "gi");
  return (
    <>
      {text.split(re).map((part, i) =>
        unverified.some((u) => u.toLowerCase() === part.toLowerCase()) ? (
          <mark key={i} title="Not in the graph result: unverified" className="rounded bg-amber-400/15 px-1 text-amber-200 underline decoration-amber-300/60 decoration-dotted">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

export function ModePill({ mode, model }: { mode: "llm" | "deterministic"; model?: string | null }) {
  return mode === "llm" ? (
    <Pill tone="violet"><Bot className="h-3 w-3" />LLM{model ? ` · ${model}` : ""}</Pill>
  ) : (
    <Pill tone="accent"><Cpu className="h-3 w-3" />Deterministic</Pill>
  );
}

export function AgentAnswer({ r, llmAvailable }: { r: AskResponse; llmAvailable: boolean }) {
  const [sum, setSum] = useState<SummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const canSummarize = llmAvailable && (r.exposed.length > 0 || r.outcome === "answered_from_memory" || !!r.spof);

  const summarize = async () => {
    setLoading(true);
    try {
      setSum(await apiPost<SummaryResponse>("/api/summary", { query: r.query, depth: r.depth, fresh: r.outcome !== "answered_from_memory" }));
    } catch (e) {
      setSum({ summary: null, unverified: [], model: null, error: (e as Error).message });
    } finally {
      setLoading(false);
    }
  };

  if (!r.llm_summary && !canSummarize) return null;
  return (
    <Card delay={0.03} className="p-5">
      {r.llm_summary && (
        <div className="flex items-start gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-violet-500/30 to-indigo-500/20 ring-1 ring-violet-400/30"><Bot className="h-4 w-4 text-violet-200" /></span>
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="text-[12px] font-medium text-white/70">Agent answer</span>
              <ModePill mode={r.mode} model={r.llm_model} />
              {r.llm_unverified.length > 0 ? (
                <Pill tone="warn"><ShieldQuestion className="h-3 w-3" />{r.llm_unverified.length} unverified name{r.llm_unverified.length > 1 ? "s" : ""} flagged</Pill>
              ) : (
                <Pill tone="safe"><CheckCircle2 className="h-3 w-3" />every name verified against the graph</Pill>
              )}
            </div>
            <p className="text-[14px] leading-relaxed text-white/80"><FlaggedText text={r.llm_summary} unverified={r.llm_unverified} /></p>
          </div>
        </div>
      )}
      {canSummarize && (
        <div className={r.llm_summary ? "mt-4 border-t border-white/[0.06] pt-4" : ""}>
          {!sum && (
            <button onClick={summarize} disabled={loading} className="inline-flex items-center gap-2 rounded-xl bg-white/[0.06] px-3 py-1.5 text-xs font-medium text-white/80 ring-1 ring-inset ring-white/10 transition hover:bg-white/10 disabled:opacity-50">
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 text-violet-300" />}
              {loading ? "Writing incident summary" : "AI incident summary"}
            </button>
          )}
          {loading && <Skeleton className="mt-3 h-16" />}
          {sum && (
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-[12px] font-medium text-white/70">AI incident summary</span>
                {sum.model && <Pill tone="violet">{sum.model}</Pill>}
                {sum.summary && (sum.unverified.length ? <Pill tone="warn">{sum.unverified.length} unverified flagged</Pill> : <Pill tone="safe">names verified</Pill>)}
                {sum.summary && <CopyButton text={sum.summary} />}
              </div>
              {sum.summary ? (
                <p className="text-[13.5px] leading-relaxed text-white/75"><FlaggedText text={sum.summary} unverified={sum.unverified} /></p>
              ) : (
                <p className="text-sm text-rose-200/80">{sum.error}</p>
              )}
            </motion.div>
          )}
        </div>
      )}
    </Card>
  );
}

export function ApproveBar({ incidentId, onApproved }: { incidentId: string; onApproved: (p: Playbook) => void }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [msg, setMsg] = useState("");
  const approve = async () => {
    setState("loading");
    try {
      const r = await apiPost<{ playbook: Playbook }>(`/api/incidents/${encodeURIComponent(incidentId)}/approve`, { approved_by: "on-call" });
      setState("done");
      onApproved(r.playbook);
    } catch (e) {
      setState("error");
      setMsg((e as Error).message);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-white/[0.07] bg-black/20 px-4 py-3">
      <BookCheck className="h-4 w-4 text-emerald-300" />
      <p className="flex-1 text-[12.5px] text-white/65">
        Approve this plan to store it as a <span className="font-mono text-white/85">:Playbook</span> in the graph. Next time a matching incident fires, PatientZero proposes it first.
      </p>
      {state === "done" ? (
        <Pill tone="safe"><CheckCircle2 className="h-3 w-3" />Approved and remembered</Pill>
      ) : (
        <button onClick={approve} disabled={state === "loading"} className="inline-flex items-center gap-2 rounded-xl bg-emerald-500/90 px-3.5 py-1.5 text-xs font-semibold text-black transition hover:bg-emerald-400 disabled:opacity-50">
          {state === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />} Approve
        </button>
      )}
      {state === "error" && <p className="w-full text-[11.5px] text-rose-200/80">{msg}</p>}
    </div>
  );
}

export function PlaybooksPanel({ playbooks, error }: { playbooks: Playbook[] | null; error: string | null }) {
  return (
    <Card delay={0.3}>
      <CardHeader icon={<BookCheck className="h-4 w-4" />} title="Approved playbooks" sub="Human-approved remediations remembered in the graph" />
      <div className="scroll-thin max-h-64 space-y-1.5 overflow-y-auto p-4">
        {error && <p className="py-3 text-center text-[12px] text-white/40">{error}</p>}
        {!error && playbooks === null && <Skeleton className="h-12" />}
        {!error && playbooks?.length === 0 && <p className="py-5 text-center text-sm text-white/40">No playbooks yet. Generate a fix, then Approve.</p>}
        {playbooks?.map((p) => (
          <div key={p.id} className="rounded-xl border border-emerald-400/10 bg-emerald-500/[0.04] px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate text-[12.5px] font-medium text-white/85">{p.name || p.trigger || p.id}</p>
              {p.approved_at && <span className="shrink-0 text-[10px] text-white/35">{new Date(p.approved_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>}
            </div>
            <p className="truncate font-mono text-[10.5px] text-emerald-200/70">
              {p.overrides ? Object.entries(p.overrides).map(([k, v]) => `${k}@${v}`).join("  ") : p.incident_id}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}
