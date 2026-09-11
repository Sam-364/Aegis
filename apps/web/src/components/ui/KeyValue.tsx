import type { ReactNode } from "react";

export interface KV {
  k: string;
  v: ReactNode;
  mono?: boolean;
  span?: 1 | 2;
}

export function KeyValue({ items, columns = 2, className = "", dense = false }: { items: KV[]; columns?: 1 | 2 | 3 | 4; className?: string; dense?: boolean }) {
  const cols = columns === 1 ? "grid-cols-1" : columns === 2 ? "grid-cols-2" : columns === 3 ? "grid-cols-3" : "grid-cols-4";
  return (
    <dl className={`grid ${cols} gap-x-4 ${dense ? "gap-y-1.5" : "gap-y-2.5"} ${className}`}>
      {items.map((it) => (
        <div key={it.k} className={`min-w-0 ${it.span === 2 ? "col-span-2" : ""}`}>
          <dt className="micro mb-0.5">{it.k}</dt>
          <dd className={`min-w-0 text-[12.5px] leading-snug break-words text-ink ${it.mono ? "mono" : ""}`}>{it.v ?? <span className="text-faint">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Compact inline arguments: `key=value` pairs in mono. */
export function ArgList({ args, className = "" }: { args: Record<string, unknown> | null | undefined; className?: string }) {
  const entries = Object.entries(args ?? {});
  if (entries.length === 0) return <span className={`mono text-[11px] text-faint ${className}`}>(no arguments)</span>;
  return (
    <span className={`mono inline-flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] ${className}`}>
      {entries.map(([k, v]) => (
        <span key={k} className="whitespace-nowrap">
          <span className="text-muted">{k}=</span>
          <span className="text-ink">{typeof v === "string" ? v : JSON.stringify(v)}</span>
        </span>
      ))}
    </span>
  );
}
