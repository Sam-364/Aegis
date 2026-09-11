import type { ReactNode } from "react";

/** Ruled section: micro label header with a hairline, content beneath. No floating cards. */
export function Section({ title, meta, actions, children, className = "", bodyClassName = "", flush = false, live = false }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; flush?: boolean; live?: boolean }) {
  return (
    <section className={`border-b border-hairline ${className}`}>
      <header className="flex h-8 items-center justify-between gap-3 border-b border-hairline px-3">
        <div className="flex items-center gap-2">
          {live ? <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" aria-hidden /> : null}
          <h2 className="micro !text-ink">{title}</h2>
          {meta ? <span className="micro-mono">{meta}</span> : null}
        </div>
        {actions ? <div className="flex items-center gap-1.5">{actions}</div> : null}
      </header>
      <div className={`${flush ? "" : "px-3 py-3"} ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function PageHeader({ kicker, title, meta, actions, children }: { kicker?: ReactNode; title: ReactNode; meta?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="border-b border-hairline bg-panel px-5 pt-4 pb-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          {kicker ? <div className="micro mb-1">{kicker}</div> : null}
          <h1 className="flex flex-wrap items-center gap-2.5 text-[18px] leading-tight font-medium tracking-[-0.005em] text-ink">{title}</h1>
          {meta ? <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted">{meta}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </header>
  );
}

/** One figure in a stats strip. */
export function Stat({ label, value, sub, tone, mono = true }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; mono?: boolean }) {
  return (
    <div className="min-w-0 border-r border-hairline px-4 py-2.5 last:border-r-0">
      <div className="micro truncate">{label}</div>
      <div className={`${mono ? "mono" : ""} mt-0.5 text-[20px] leading-none font-medium tabular-nums`} style={{ color: tone ?? "#e8e6e1" }}>
        {value}
      </div>
      {sub ? <div className="mono mt-1 truncate text-[10.5px] text-muted">{sub}</div> : null}
    </div>
  );
}
