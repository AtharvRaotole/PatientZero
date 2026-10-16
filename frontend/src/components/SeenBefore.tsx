"use client";
import { motion } from "framer-motion";
import { History } from "lucide-react";
import type { SeenBefore as SeenBeforeT } from "@/lib/types";

/** Graph memory shown NEXT TO a fresh traversal: the result above is always freshly computed. */
export function SeenBefore({ sb }: { sb: SeenBeforeT }) {
  const added = sb.new_since ?? [];
  const gone = sb.no_longer_exposed ?? [];
  const diffKnown = sb.same_trigger && !!sb.new_since;
  const changed = added.length > 0 || gone.length > 0;
  return (
    <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, ease: "easeOut" }}
      className="rounded-lg border border-line bg-surface px-4 py-3 text-[13.5px]" data-testid="seen-before">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <History className="h-4 w-4 shrink-0 translate-y-[3px] text-ink-3" />
        <span className="font-medium text-ink">Seen before</span>
        <span className="font-mono text-[12.5px] text-ink-2">{sb.id}</span>
        <span className="text-ink-2">
          {sb.at_local}{sb.same_trigger ? "" : `, related trigger: ${sb.trigger}`}. {sb.exposed_count} service{sb.exposed_count === 1 ? "" : "s"} at depth {sb.depth}{sb.has_patch ? ", patch on file" : ""}.
        </span>
        {diffKnown && !changed && <span className="text-ink-3">No change since then.</span>}
        <span className="ml-auto text-[12.5px] text-ink-3">This report is a fresh traversal</span>
      </div>
      {diffKnown && changed && (
        <div className="mt-2 space-y-1 border-t border-line pl-7 pt-2 text-[13px]">
          {added.length > 0 && (
            <p className="text-ink-2"><span className="font-medium text-signal-ink">{added.length} newly exposed</span> <span className="font-mono text-[12.5px]">{added.join(", ")}</span></p>
          )}
          {gone.length > 0 && (
            <p className="text-ink-2"><span className="font-medium text-ink">{gone.length} no longer exposed</span> <span className="font-mono text-[12.5px]">{gone.join(", ")}</span></p>
          )}
        </div>
      )}
    </motion.div>
  );
}
