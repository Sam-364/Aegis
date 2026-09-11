"use client";

import { useNow, useMounted } from "@/lib/use-now";
import { fmtClock, fmtDateTime, fmtRelative, parseDate } from "@/lib/format";

export function RelativeTime({ iso, className = "" }: { iso: string | null | undefined; className?: string }) {
  const now = useNow(5_000);
  if (!iso) return <span className={`text-faint ${className}`}>—</span>;
  return (
    <time dateTime={iso} title={fmtDateTime(iso)} className={`mono whitespace-nowrap ${className}`} suppressHydrationWarning>
      {fmtRelative(iso, now)}
    </time>
  );
}

/**
 * Live duration. Ticks every second while `end` is null; otherwise shows the fixed span.
 * Renders as HH:MM:SS (or MM:SS) in the mono face.
 */
export function Duration({ start, end, live = true, className = "", fallbackSeconds }: { start: string | null | undefined; end?: string | null; live?: boolean; className?: string; fallbackSeconds?: number | null }) {
  const running = live && !end;
  const now = useNow(running ? 1000 : 60_000);
  const s = parseDate(start);
  let seconds: number | null = null;
  if (s) {
    const e = end ? parseDate(end) : null;
    seconds = ((e ? e.getTime() : now) - s.getTime()) / 1000;
  } else if (fallbackSeconds != null) {
    seconds = fallbackSeconds;
  }
  return (
    <span className={`mono whitespace-nowrap tabular-nums ${running ? "text-amber" : ""} ${className}`} suppressHydrationWarning title={start ? fmtDateTime(start) : undefined}>
      {fmtClock(seconds)}
    </span>
  );
}

/** Countdown to `until`; turns red inside the last minute. */
export function Countdown({ until, className = "" }: { until: string | null | undefined; className?: string }) {
  const now = useNow(1000);
  const u = parseDate(until);
  if (!u) return <span className={`mono ${className}`}>—</span>;
  const remaining = Math.max(0, (u.getTime() - now) / 1000);
  return (
    <span className={`mono tabular-nums ${remaining < 60 ? "text-sev1" : remaining < 300 ? "text-sev2" : "text-ink"} ${className}`} suppressHydrationWarning>
      {remaining <= 0 ? "expired" : fmtClock(remaining)}
    </span>
  );
}

/** UTC wall clock for the top strip. */
export function UtcClock({ className = "" }: { className?: string }) {
  const now = useNow(1000);
  const mounted = useMounted();
  if (!mounted) return <span className={`mono text-muted ${className}`}>--:--:--Z</span>;
  const d = new Date(now);
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    <span className={`mono tabular-nums text-muted ${className}`} suppressHydrationWarning title={d.toISOString()}>
      {p(d.getUTCHours())}:{p(d.getUTCMinutes())}:{p(d.getUTCSeconds())}Z
    </span>
  );
}
