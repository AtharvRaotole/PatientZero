"use client";
import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { Building2, Check, ChevronDown, CornerDownRight, MessagesSquare, Plus, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Followup, SessionHistory } from "@/lib/types";
import type { TenantInfo } from "@/lib/tenant";

const EASE = [0.22, 1, 0.36, 1] as const;

// ------------------------------------------------------------------ Tenant switcher

/** Header menu: which company's fleet graph every query runs against. */
export function TenantSwitcher({ tenants, value, onChange, services }: {
  tenants: TenantInfo[]; value: string; onChange: (t: string) => void; services?: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);
  const cur = tenants.find((t) => t.id === value);
  const name = cur?.company ?? value.charAt(0).toUpperCase() + value.slice(1);
  return (
    <div ref={ref} className="relative" data-testid="tenant-switcher" data-tenant={value}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}
        className="press inline-flex h-8 items-center gap-2 rounded-lg px-2 text-[13px] font-medium text-ink ring-1 ring-inset ring-line hover:bg-subtle">
        <span className="grid h-5 w-5 place-items-center rounded-[5px] bg-ink text-[10.5px] font-semibold text-paper">{name.charAt(0)}</span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={name} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -3 }} transition={{ duration: 0.14 }}>{name}</motion.span>
        </AnimatePresence>
        <ChevronDown className={clsx("h-3.5 w-3.5 text-ink-3 transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div role="menu" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.14, ease: EASE }}
            className="absolute left-0 top-[calc(100%+6px)] z-40 w-[260px] overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-md">
            <p className="px-2.5 pb-1 pt-1.5 text-[11.5px] font-medium text-ink-3">Tenant, one FalkorDB graph each</p>
            {tenants.map((t) => (
              <button key={t.id} role="menuitemradio" aria-checked={t.id === value} data-tenant-option={t.id} disabled={!t.loaded}
                onClick={() => { setOpen(false); onChange(t.id); }}
                className={clsx("flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors disabled:opacity-40", t.id === value ? "bg-subtle" : "hover:bg-subtle/70")}>
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-subtle text-[12px] font-semibold text-ink ring-1 ring-inset ring-line">{t.company.charAt(0)}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium text-ink">{t.company}</span>
                  <span className="block truncate font-mono text-[11.5px] text-ink-3">{t.graph}{services?.[t.id] ? ` · ${services[t.id]} services` : ""}</span>
                </span>
                {t.id === value && <Check className="h-4 w-4 text-ink" />}
              </button>
            ))}
            <p className="flex items-center gap-1.5 border-t border-line px-2.5 pb-1.5 pt-2 text-[11.5px] text-ink-3"><Building2 className="h-3 w-3" />Fleets, memory and sessions never cross tenants.</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ------------------------------------------------------------------ Follow-up resolution

const REFERS = /\b(that|it|this|those|them|same|again)\b/i;

/** "debug@4.4.2", or a campaign key like 2025-09-15-shai-hulud shown as Shai-Hulud. */
export function anchorLabel(a?: { kind: string; key: string }, campaignName?: string): string {
  if (!a) return "";
  if (a.kind === "campaign") return campaignName ?? a.key.replace(/^\d{4}-\d{2}-\d{2}-/, "").split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join("-");
  if (a.kind === "maintainer") return `maintainer ${a.key}`;
  return a.key;
}

/** One quiet line under the headline: how a follow-up was read against the session. */
export function FollowupLine({ f, campaignName }: { f?: Followup | null; campaignName?: string }) {
  if (!f) return null;
  if (!f.resolved) {
    return (
      <p data-testid="followup-line" className="mt-2.5 flex items-center gap-1.5 text-[13px] text-ink-3">
        <CornerDownRight className="h-3.5 w-3.5" />Ran as typed: {f.reason ?? "nothing earlier to refer to"}.
      </p>
    );
  }
  const what = anchorLabel(f.anchor, campaignName);
  const word = f.original?.match(REFERS)?.[1]?.toLowerCase();
  const depthOnly = !word && /depth\s*\d/i.test(f.original ?? "");
  return (
    <p data-testid="followup-line" title={f.reason} className="mt-2.5 flex flex-wrap items-center gap-x-1.5 text-[13.5px] text-ink-2">
      <CornerDownRight className="h-3.5 w-3.5 text-ink-3" />
      {depthOnly ? (
        <>Reran <span className="font-mono text-[13px] text-ink">{what}</span> from turn {f.from_turn} at depth {f.depth}.</>
      ) : (
        <>Resolved {word ? <span className="italic">{word}</span> : "the question"} to <span className="font-mono text-[13px] text-ink">{what}</span> from turn {f.from_turn}{f.depth && f.depth !== 4 ? `, depth ${f.depth}` : ""}.</>
      )}
    </p>
  );
}

// ------------------------------------------------------------------ Session rail

/** Compact history of this tenant's conversation, read back from the graph. */
export function SessionRail({ history, error, onPick, onNew, busy }: {
  history: SessionHistory | null; error?: string | null; onPick: (q: string) => void; onNew: () => void; busy?: boolean;
}) {
  const turns = history?.history ?? [];
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, [turns.length]);
  return (
    <aside data-testid="session-rail" className="flex max-h-[340px] flex-col rounded-xl border border-line bg-surface shadow-xs">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3.5 py-2.5">
        <span className="flex items-center gap-2 text-[13px] font-medium text-ink"><MessagesSquare className="h-3.5 w-3.5 text-ink-3" />Session
          <span className="font-mono text-[11.5px] font-normal text-ink-3 tabular">{turns.length ? `${turns.length} turn${turns.length > 1 ? "s" : ""}` : ""}</span>
        </span>
        <button type="button" onClick={onNew} disabled={busy} data-testid="new-session"
          className="press inline-flex h-7 items-center gap-1 rounded-md px-2 text-[12px] font-medium text-ink-2 hover:bg-subtle hover:text-ink disabled:opacity-40">
          <Plus className="h-3.5 w-3.5" />New session
        </button>
      </div>
      {turns.length === 0 ? (
        <p className="px-3.5 py-3 text-[12.5px] leading-relaxed text-ink-3">
          {error ?? <>Each question becomes a turn in the graph. Follow-ups such as <span className="text-ink-2">&ldquo;who should I wake for that?&rdquo;</span> or <span className="text-ink-2">&ldquo;and at depth 6?&rdquo;</span> resolve against it.</>}
        </p>
      ) : (
        <ol ref={listRef} className="scroll-thin flex-1 overflow-y-auto py-1">
          {turns.map((t) => (
            <motion.li key={t.i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.18 }}>
              <button type="button" onClick={() => onPick(t.query)} title={t.message}
                className="group flex w-full gap-2.5 px-3.5 py-1.5 text-left hover:bg-subtle/70" data-turn={t.i}>
                <span className="mt-[1px] w-4 shrink-0 text-right font-mono text-[11px] text-ink-4 tabular">{t.i}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    {t.kind === "crew" && <Users className="h-3 w-3 shrink-0 text-ink-3" />}
                    <span className="truncate text-[12.5px] text-ink">{t.query}</span>
                  </span>
                  {t.followup_from && t.resolved_query !== t.query && (
                    <span className="block truncate text-[11.5px] text-ink-3">from turn {t.followup_from}: {t.resolved_query}</span>
                  )}
                </span>
                <TurnBadge t={t} />
              </button>
            </motion.li>
          ))}
        </ol>
      )}
    </aside>
  );
}

function TurnBadge({ t }: { t: SessionHistory["history"][number] }) {
  const n = t.exposed_count;
  if (n !== null && n !== undefined && n > 0) return <span className="shrink-0 font-mono text-[11.5px] font-semibold text-signal-ink tabular">{n}</span>;
  if (t.outcome === "not_in_graph" || n === 0) return <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-safe" title="Not hit" />;
  return <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-line-strong" />;
}
