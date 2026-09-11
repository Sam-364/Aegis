export function Skeleton({ rows = 4, className = "" }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 px-3 py-3 ${className}`} aria-busy>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-3 rounded-xs bg-panel-2 animate-pulse" style={{ width: `${90 - ((i * 17) % 40)}%` }} />
      ))}
    </div>
  );
}

export function InlineSpinner({ label = "loading" }: { label?: string }) {
  return (
    <span className="micro inline-flex items-center gap-1.5">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" />
      {label}
    </span>
  );
}
