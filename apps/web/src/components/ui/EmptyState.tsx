import type { ReactNode } from "react";

export function EmptyState({ title, hint, action, className = "", compact = false }: { title: string; hint?: ReactNode; action?: ReactNode; className?: string; compact?: boolean }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "py-6" : "py-14"} ${className}`}>
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden className="mb-3 text-faint">
        <rect x="3.5" y="3.5" width="21" height="21" rx="2" fill="none" stroke="currentColor" strokeWidth="1" />
        <path d="M8 14h12M14 8v12" stroke="currentColor" strokeWidth="1" strokeDasharray="2 2" />
      </svg>
      <div className="text-[12.5px] font-medium text-ink">{title}</div>
      {hint ? <div className="mt-1 max-w-sm text-[11.5px] leading-snug text-muted">{hint}</div> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
