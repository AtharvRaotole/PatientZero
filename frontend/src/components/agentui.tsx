"use client";
import { motion } from "framer-motion";
import { BookCheck, CheckCircle2, Loader2, ShieldQuestion, Sparkles } from "lucide-react";
import { useState } from "react";
import { apiPost } from "@/lib/api";
import type { AskResponse, Playbook, SummaryResponse } from "@/lib/types";
import { Button, Card, CardHeader, CopyButton, Skeleton } from "./ui";

/** Text with any unverified graph names highlighted (they were not in the tool results). */
export function FlaggedText({ text, unverified }: { text: string; unverified: string[] }) {
  if (!unverified.length) return <>{text}</>;
  const esc = unverified.map((u) => u.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
  const re = new RegExp(`(${esc.join("|")})`, "gi");
  return (
    <>
      {text.split(re).map((part, i) =>
        unverified.some((u) => u.toLowerCase() === part.toLowerCase()) ? (
          <mark key={i} title="Not in the graph result: unverified" className="rounded bg-risk-soft px-1 text-risk underline decoration-risk decoration-dotted">
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
  return <span className="text-[12.5px] text-ink-3">{mode === "llm" ? `LLM${model ? ` ${model}` : ""}` : "Deterministic"}</span>;
}

function Verified({ unverified }: { unverified: string[] }) {
  return unverified.length ? (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-risk"><ShieldQuestion className="h-3.5 w-3.5" />{unverified.length} unverified name{unverified.length > 1 ? "s" : ""} flagged</span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-3"><CheckCircle2 className="h-3.5 w-3.5 text-safe" />Every name checked against the graph</span>
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
    <div className="space-y-3">
      {r.llm_summary && (
        <div className="rounded-xl border border-line bg-surface p-5">
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-[13px] font-medium text-ink">Agent answer</span>
            <ModePill mode={r.mode} model={r.llm_model} />
            <Verified unverified={r.llm_unverified} />
          </div>
          <p className="text-[15px] leading-relaxed text-ink"><FlaggedText text={r.llm_summary} unverified={r.llm_unverified} /></p>
        </div>
      )}
      {canSummarize && !sum && (
        <Button onClick={summarize} disabled={loading}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 text-ink-3" />}
          {loading ? "Writing a summary" : "Summarize for the incident channel"}
        </Button>
      )}
      {loading && <Skeleton className="h-16" />}
      {sum && (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }} className="rounded-xl border border-line bg-surface p-5">
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-[13px] font-medium text-ink">Incident summary</span>
            {sum.model && <span className="text-[12.5px] text-ink-3">{sum.model}</span>}
            {sum.summary && <Verified unverified={sum.unverified} />}
            {sum.summary && <span className="ml-auto"><CopyButton text={sum.summary} /></span>}
          </div>
          {sum.summary ? (
            <p className="text-[14.5px] leading-relaxed text-ink-2"><FlaggedText text={sum.summary} unverified={sum.unverified} /></p>
          ) : (
            <p className="text-[13.5px] text-signal-ink">{sum.error}</p>
          )}
        </motion.div>
      )}
    </div>
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
    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-line px-4 py-3">
      <p className="flex-1 text-[13px] leading-relaxed text-ink-2">
        Approve this plan to keep it as a <span className="font-mono text-ink">:Playbook</span> in the graph. The next matching incident proposes it first.
      </p>
      {state === "done" ? (
        <motion.span initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} className="inline-flex items-center gap-1.5 text-[13px] font-medium text-safe">
          <CheckCircle2 className="h-4 w-4" />Approved and remembered
        </motion.span>
      ) : (
        <Button variant="primary" onClick={approve} disabled={state === "loading"}>
          {state === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <BookCheck className="h-3.5 w-3.5" />} Approve
        </Button>
      )}
      {state === "error" && <p className="w-full text-[12.5px] text-signal-ink">{msg}</p>}
    </div>
  );
}

export function PlaybooksPanel({ playbooks, error }: { playbooks: Playbook[] | null; error: string | null }) {
  return (
    <Card>
      <CardHeader icon={<BookCheck />} title="Approved playbooks" sub="Remediations a human approved, remembered in the graph." />
      <div className="scroll-thin max-h-72 divide-y divide-line overflow-y-auto">
        {error && <p className="px-5 py-4 text-[13px] text-ink-3">{error}</p>}
        {!error && playbooks === null && <div className="p-4"><Skeleton className="h-10" /></div>}
        {!error && playbooks?.length === 0 && <p className="px-5 py-4 text-[13px] text-ink-3">No playbooks yet. Generate a fix, then approve it.</p>}
        {playbooks?.map((p) => (
          <motion.div key={p.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="px-5 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <p className="truncate text-[13.5px] font-medium text-ink">{p.name || p.trigger || p.id}</p>
              {p.approved_at && <span className="shrink-0 text-[12px] text-ink-3">{p.approved_by ? `${p.approved_by}, ` : ""}{new Date(p.approved_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>}
            </div>
            <p className="mt-1 truncate font-mono text-[12px] text-ink-2">
              {p.overrides ? Object.entries(p.overrides).map(([k, v]) => `${k}@${v}`).join("   ") : p.incident_id}
            </p>
          </motion.div>
        ))}
      </div>
    </Card>
  );
}
