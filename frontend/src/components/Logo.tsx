/* PatientZero mark: a ring (the zero) with one vermilion node sitting on it and a hairline edge leaving it.
   Drawn on a 24-unit grid so it stays crisp at 16, 24 and 48 px. */
export function Mark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <circle cx="10" cy="14" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="M15 9 L21.25 2.75" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
      <circle cx="21.25" cy="2.75" r="1.25" fill="currentColor" />
      <circle cx="15" cy="9" r="3" fill="#E5484D" stroke="#FAFAF9" strokeWidth="1.25" />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 text-ink ${className ?? ""}`}>
      <Mark size={22} />
      <span className="text-[15px] font-semibold tracking-[-0.02em]">PatientZero</span>
    </span>
  );
}
