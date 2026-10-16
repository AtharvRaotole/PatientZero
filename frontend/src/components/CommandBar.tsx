"use client";
import { Command, defaultFilter } from "cmdk";
import { AnimatePresence, motion } from "framer-motion";
import { CornerDownLeft, Loader2, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export type Suggestion = { q: string; hint: string; group: string };

/** Keyboard-first command bar. Typing then Enter asks the typed question; arrows pick a suggestion. */
export function CommandBar({ value, onChange, onRun, suggestions, disabled, loading, inputRef }: {
  value: string; onChange: (v: string) => void; onRun: (q: string, fromSuggestion: boolean) => void;
  suggestions: Suggestion[]; disabled?: boolean; loading?: boolean; inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("");
  const wrap = useRef<HTMLDivElement>(null);
  const groups = [...new Set(suggestions.map((s) => s.group))];
  const typed = value.trim();

  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const run = (q: string, fromSuggestion: boolean) => {
    if (!q.trim() || disabled) return;
    onChange(q);
    setOpen(false);
    inputRef?.current?.blur();
    onRun(q, fromSuggestion);
  };

  return (
    <div ref={wrap} className="relative">
      {/* "Ask <typed text>" always ranks first, so Enter asks exactly what was typed and never swaps in an example. */}
      <Command label="Ask PatientZero" value={active} onValueChange={setActive} loop
        filter={(v, search, keywords) => (v.startsWith("ask:") ? 1 : defaultFilter(v, search, keywords) * 0.99)}
        className="rounded-xl border border-line bg-surface shadow-sm transition-shadow focus-within:border-line-strong focus-within:shadow-md">
        <div className="flex h-14 items-center gap-3 pl-4 pr-2">
          <Search className="h-[18px] w-[18px] shrink-0 text-ink-3" />
          <Command.Input
            ref={inputRef}
            value={value}
            onValueChange={(v) => { onChange(v); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { setOpen(false); return; }
              if (e.key === "Enter" && !open) { e.preventDefault(); run(value, false); }
            }}
            placeholder="Ask about a maintainer, package@version, advisory or service"
            aria-label="Ask PatientZero"
            className="h-full flex-1 bg-transparent text-[16px] text-ink placeholder:text-ink-3 focus:outline-none focus-visible:outline-none"
          />
          <kbd className="hidden items-center rounded-md border border-line px-1.5 py-0.5 font-sans text-[11px] text-ink-3 sm:inline-flex">⌘K</kbd>
          <button type="button" onClick={() => run(value, false)} disabled={disabled || loading || !typed}
            className="press inline-flex h-10 items-center gap-2 rounded-lg bg-ink px-4 text-[14px] font-medium text-paper hover:bg-ink/85 disabled:opacity-35">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <CornerDownLeft className="h-4 w-4" />} Trace
          </button>
        </div>
        <AnimatePresence>
          {open && !disabled && (
            <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.15, ease: "easeOut" }}
              className="absolute inset-x-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
              <Command.List className="scroll-thin max-h-[340px] overflow-y-auto p-1.5">
                {typed && (
                  <Command.Group>
                    <Command.Item value={`ask:${typed}`} onSelect={() => run(typed, false)}
                      className="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-[14px] text-ink data-[selected=true]:bg-subtle">
                      <CornerDownLeft className="h-4 w-4 text-ink-3" />
                      <span className="truncate">Ask <span className="font-medium">{typed}</span></span>
                    </Command.Item>
                  </Command.Group>
                )}
                <Command.Empty className="px-3 py-2.5 text-[13px] text-ink-3">No matching examples. Press Enter to ask.</Command.Empty>
                {groups.map((g) => (
                  <Command.Group key={g} heading={g}
                    className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:text-[12px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-ink-3">
                    {suggestions.filter((s) => s.group === g).map((s) => (
                      <Command.Item key={s.q} value={s.q} keywords={[s.hint]} onSelect={() => run(s.q, true)}
                        className="flex cursor-pointer items-center justify-between gap-4 rounded-lg px-3 py-2 data-[selected=true]:bg-subtle">
                        <span className="truncate font-mono text-[13px] text-ink">{s.q}</span>
                        <span className="hidden shrink-0 text-[12.5px] text-ink-3 sm:block">{s.hint}</span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                ))}
              </Command.List>
              <div className="flex items-center gap-4 border-t border-line px-3 py-2 text-[12px] text-ink-3">
                <span><kbd className="font-sans">↑↓</kbd> to choose</span>
                <span><kbd className="font-sans">↵</kbd> to trace</span>
                <span><kbd className="font-sans">esc</kbd> to close</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Command>
    </div>
  );
}
