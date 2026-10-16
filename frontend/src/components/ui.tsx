"use client";
import { animate, motion, useMotionValue, useTransform } from "framer-motion";
import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import clsx from "clsx";

/** Animated number: counts from the previous value to `value` (ease-out), respects reduced motion. */
export function CountUp({ value, className, duration = 0.9 }: { value: number; className?: string; duration?: number }) {
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => Math.round(v).toLocaleString());
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      mv.set(value);
      return;
    }
    const c = animate(mv, value, { duration, ease: [0.16, 1, 0.3, 1] });
    return () => c.stop();
  }, [value, duration, mv]);
  return <motion.span className={clsx("tabular", className)}>{text}</motion.span>;
}

export function Card({ className, children, delay = 0 }: { className?: string; children: React.ReactNode; delay?: number }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
      className={clsx("glass rounded-2xl", className)}
    >
      {children}
    </motion.section>
  );
}

export function CardHeader({ icon, title, right, sub }: { icon: React.ReactNode; title: string; right?: React.ReactNode; sub?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-3.5">
      <div className="flex items-center gap-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-white/[0.06] text-white/80">{icon}</span>
        <div>
          <h3 className="text-[13.5px] font-semibold tracking-[-0.01em] text-white/92">{title}</h3>
          {sub && <p className="mt-0.5 text-[11.5px] leading-snug text-white/42">{sub}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

export function Pill({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: "neutral" | "danger" | "safe" | "accent" | "warn" | "violet"; className?: string }) {
  const tones = {
    neutral: "bg-white/[0.06] text-white/70 ring-white/10",
    danger: "bg-rose-500/10 text-rose-300 ring-rose-400/25",
    safe: "bg-emerald-500/10 text-emerald-300 ring-emerald-400/25",
    accent: "bg-indigo-500/10 text-indigo-200 ring-indigo-400/25",
    warn: "bg-amber-500/10 text-amber-200 ring-amber-400/25",
    violet: "bg-violet-500/10 text-violet-200 ring-violet-400/25",
  };
  return <span className={clsx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone], className)}>{children}</span>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          /* clipboard blocked: ignore */
        }
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
      className="press inline-flex items-center gap-1.5 rounded-lg bg-white/[0.06] px-2.5 py-1 text-[11px] font-medium text-white/75 ring-1 ring-inset ring-white/10 hover:bg-white/10 hover:text-white"
    >
      {done ? <Check className="h-3.5 w-3.5 text-emerald-300" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? "Copied" : label}
    </button>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("shimmer rounded-lg", className)} />;
}

/** A dependency chain rendered as chips, with the decisive last edge called out. */
export function PathChain({ via, requirement, bad, exposed, compact }: { via: string[]; requirement: string | null; bad?: string; exposed: boolean | null; compact?: boolean }) {
  const last = via[via.length - 1] ?? "";
  const pkg = last.startsWith("@") ? "@" + last.slice(1).split("@")[0] : last.split("@")[0];
  return (
    <div className={clsx("flex flex-wrap items-center gap-1 font-mono", compact ? "text-[10.5px]" : "text-[11.5px]")}>
      {via.slice(0, -1).map((n, i) => (
        <span key={i} className="flex items-center gap-1">
          <span className={clsx("rounded-md px-1.5 py-0.5", i === 0 ? "bg-indigo-500/15 text-indigo-200" : "bg-white/[0.05] text-white/70")}>{n}</span>
          <span className="text-white/25">→</span>
        </span>
      ))}
      <span className={clsx("rounded-md px-1.5 py-0.5 ring-1 ring-inset", exposed ? "bg-rose-500/15 text-rose-200 ring-rose-400/30" : "bg-emerald-500/10 text-emerald-200 ring-emerald-400/20")}>
        {pkg} <span className="opacity-70">{requirement}</span>
        {bad && <span className={clsx("ml-1 font-semibold", exposed ? "text-rose-300" : "text-emerald-300")}>{exposed ? `⚠ ${bad}` : `✓ excludes ${bad}`}</span>}
      </span>
    </div>
  );
}
