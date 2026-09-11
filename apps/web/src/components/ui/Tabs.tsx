"use client";

export interface TabDef<T extends string> {
  id: T;
  label: string;
  count?: number | null;
  live?: boolean;
}

export function Tabs<T extends string>({ tabs, value, onChange, className = "" }: { tabs: TabDef<T>[]; value: T; onChange: (id: T) => void; className?: string }) {
  return (
    <div role="tablist" className={`flex items-end gap-0 border-b border-hairline ${className}`}>
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={`-mb-px flex h-8 items-center gap-1.5 border-b px-3 font-sans text-[11px] font-medium tracking-[0.06em] uppercase transition-colors ${
              active ? "border-amber text-ink" : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {t.live ? <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" /> : null}
            {t.label}
            {t.count != null ? <span className={`mono text-[10px] ${active ? "text-amber" : "text-faint"}`}>{t.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
