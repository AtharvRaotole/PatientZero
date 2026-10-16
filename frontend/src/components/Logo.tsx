/* PatientZero mark: an upright zero with one vermilion node on its shoulder and an edge leaving it, like a node
   that has already reached out. 24-unit grid; the 2.5 stroke matches the semibold wordmark and holds at 16 px. */
export function Mark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <ellipse cx="10.5" cy="13.25" rx="6.1" ry="8.25" stroke="currentColor" strokeWidth="2.5" />
      <path d="M15.6 7.4 L20.9 2.6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
      <circle cx="20.9" cy="2.6" r="1.6" fill="currentColor" />
      <circle cx="15.1" cy="7.75" r="3.5" fill="#E5484D" stroke="#FAFAF9" strokeWidth="1.6" />
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
