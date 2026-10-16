"use client";
import { motion } from "framer-motion";
import { History } from "lucide-react";
import type { SeenBefore as SeenBeforeT } from "@/lib/types";

/** Graph memory shown NEXT TO a fresh traversal: the result above is always freshly computed. */
export function SeenBefore({ sb }: { sb: SeenBeforeT }) {
  const added = sb.new_since ?? [];
  const gone = sb.no_longer_exposed ?? [];
  return (
    <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-surface px-4 py-2.5 text-[13.5px] text-ink-2"
      data-testid="seen-before">
      <History className="h-4 w-4 shrink-0 text-ink-3" />
      <span>
        <span className="font-medium text-ink">Seen before: {sb.id}</span>
        <span className="text-ink-2"> on {sb.at_local}{sb.same_trigger ? "" : ` (related: ${sb.trigger})`}, {sb.exposed_count} service{sb.exposed_count === 1 ? "" : "s"} at depth {sb.depth}{sb.has_patch ? ", patch on file" : ""}</span>
      </span>
      {sb.same_trigger && sb.new_since && (
        <span className="text-xs text-ink-2">
          {added.length === 0 && gone.length === 0 ? "No change since then." : [
            added.length ? `${added.length} newly exposed: ${added.join(", ")}` : "",
            gone.length ? `${gone.length} no longer exposed: ${gone.join(", ")}` : "",
          ].filter(Boolean).join(". ")}
        </span>
      )}
      <span className="ml-auto text-[12px] text-ink-3">Result above is a fresh traversal</span>
    </motion.div>
  );
}
