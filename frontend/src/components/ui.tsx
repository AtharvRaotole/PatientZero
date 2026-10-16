"use client";
import { animate, motion, useMotionValue, useTransform } from "framer-motion";
import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import clsx from "clsx";

const EASE = [0.22, 1, 0.36, 1] as const;

/** Animated number: counts up to `value` (ease-out). Respects reduced motion. */
export function CountUp({ value, className, duration = 0.8 }: { value: number; className?: string; duration?: number }) {
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
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay, ease: EASE }}
      className={clsx("surface rounded-xl shadow-xs", className)}
    >
      {children}
    </motion.section>
  );
}

export function CardHeader({ icon, title, right, sub }: { icon?: React.ReactNode; title: string; right?: React.ReactNode; sub?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-3.5">
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && <span className="text-ink-3 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>}
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
          {sub && <p className="mt-0.5 truncate text-[12.5px] text-ink-3">{sub}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

type Tone = "neutral" | "danger" | "safe" | "accent" | "warn" | "violet";
export function Pill({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: Tone; className?: string }) {
  const tones: Record<Tone, string> = {
    neutral: "bg-subtle text-ink-2 ring-line",
    danger: "bg-signal-soft text-signal-ink ring-signal/25",
    safe: "bg-safe-soft text-safe ring-safe/20",
    accent: "bg-subtle text-ink-2 ring-line",
    warn: "bg-risk-soft text-risk ring-risk/20",
    violet: "bg-subtle text-ink-2 ring-line",
  };
  return <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[12px] font-medium ring-1 ring-inset", tones[tone], className)}>{children}</span>;
}

/** Status dot + text, for the top bar and inline states. */
export function Status({ tone = "neutral", children }: { tone?: "neutral" | "danger" | "safe" | "warn"; children: React.ReactNode }) {
  const dot = { neutral: "bg-ink-3", danger: "bg-signal", safe: "bg-safe", warn: "bg-risk" }[tone];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] text-ink-2">
      <span className={clsx("h-1.5 w-1.5 rounded-full", dot)} />
      {children}
    </span>
  );
}

export function Button({ children, onClick, disabled, variant = "secondary", className, type = "button", title }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "ghost"; className?: string; type?: "button" | "submit"; title?: string;
}) {
  const v = {
    primary: "bg-ink text-paper hover:bg-ink/85 shadow-xs",
    secondary: "bg-surface text-ink ring-1 ring-inset ring-line hover:bg-subtle shadow-xs",
    ghost: "text-ink-2 hover:bg-subtle hover:text-ink",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
      className={clsx("press inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-[13px] font-medium disabled:pointer-events-none disabled:opacity-40", v, className)}>
      {children}
    </button>
  );
}

/** Segmented control (radio-like), used for tabs and mode switches. */
export function Segmented<T extends string>({ value, onChange, options, size = "md" }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: React.ReactNode }[]; size?: "sm" | "md";
}) {
  return (
    <div role="tablist" className="inline-flex rounded-lg bg-subtle p-0.5 ring-1 ring-inset ring-line">
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={clsx("press relative whitespace-nowrap rounded-md font-medium", size === "sm" ? "px-2 py-0.5 text-[12px]" : "px-2.5 py-1 text-[13px]",
            value === o.value ? "text-ink" : "text-ink-3 hover:text-ink-2")}>
          {value === o.value && <motion.span layoutId={`seg-${options.map((x) => x.value).join("")}`} className="absolute inset-0 rounded-md bg-surface shadow-xs ring-1 ring-line" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          /* clipboard blocked: ignore */
        }
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
      className="h-7 px-2.5 text-[12px]"
    >
      {done ? <Check className="h-3.5 w-3.5 text-safe" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? "Copied" : label}
    </Button>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("shimmer rounded-md", className)} />;
}

/** A numbered section of the incident report: number and title on the left, content on the right. */
export function ReportSection({ n, title, sub, children, delay = 0, id }: { n: number; title: string; sub?: React.ReactNode; children: React.ReactNode; delay?: number; id?: string }) {
  return (
    <motion.section id={id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay, ease: EASE }}
      className="grid gap-4 border-t border-line py-10 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-10">
      <header className="lg:sticky lg:top-20 lg:self-start">
        <p className="font-mono text-[12px] text-ink-3 tabular">{String(n).padStart(2, "0")}</p>
        <h2 className="mt-1 text-[20px] font-semibold tracking-[-0.02em] text-ink">{title}</h2>
        {sub && <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">{sub}</p>}
      </header>
      <div className="min-w-0 space-y-4">{children}</div>
    </motion.section>
  );
}

/** A dependency chain rendered as mono text, with the decisive last edge called out. */
export function PathChain({ via, requirement, bad, exposed, compact }: { via: string[]; requirement: string | null; bad?: string; exposed: boolean | null; compact?: boolean }) {
  const last = via[via.length - 1] ?? "";
  const pkg = last.startsWith("@") ? "@" + last.slice(1).split("@")[0] : last.split("@")[0];
  return (
    <div className={clsx("flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono", compact ? "text-[12px]" : "text-[12.5px]")}>
      {via.slice(0, -1).map((n, i) => (
        <span key={i} className="flex items-center gap-1.5">
          <span className={i === 0 ? "font-medium text-ink" : "text-ink-2"}>{n}</span>
          <span className="text-ink-3">→</span>
        </span>
      ))}
      <span className={clsx("rounded px-1.5 py-px", exposed ? "bg-signal-soft text-signal-ink" : "bg-subtle text-ink-2")}>
        {pkg} <span className="opacity-75">{requirement}</span>
        {bad && <span className={clsx("ml-1.5 font-semibold", exposed ? "text-signal-ink" : "text-safe")}>{exposed ? `admits ${bad}` : `excludes ${bad}`}</span>}
      </span>
    </div>
  );
}
