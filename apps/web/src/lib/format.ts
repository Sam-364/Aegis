/** Formatting helpers. All numeric output is meant for the mono face with tabular numerals. */

const pad2 = (n: number): string => String(n).padStart(2, "0");

export function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** HH:MM:SS in local time. */
export function fmtTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "--:--:--";
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** HH:MM:SS.mmm in local time. */
export function fmtTimeMs(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "--:--:--.---";
  return `${fmtTime(iso)}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

/** YYYY-MM-DD HH:MM:SS in local time. */
export function fmtDateTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${fmtTime(iso)}`;
}

export function fmtDate(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Compact duration: 42s, 3m 08s, 1h 02m, 2d 04h. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${pad2(s % 60)}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${pad2(m % 60)}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${pad2(h % 24)}h`;
}

/** Clock-style duration HH:MM:SS (grows to include hours only when needed). */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${pad2(h)}:${pad2(m)}:${pad2(sec)}` : `${pad2(m)}:${pad2(sec)}`;
}

export function fmtRelative(iso: string | null | undefined, now: number = Date.now()): string {
  const d = parseDate(iso);
  if (!d) return "—";
  const diff = Math.round((now - d.getTime()) / 1000);
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? "ago" : "from now";
  if (abs < 5) return diff >= 0 ? "just now" : "now";
  if (abs < 60) return `${abs}s ${suffix}`;
  if (abs < 3600) return `${Math.floor(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.floor(abs / 3600)}h ${suffix}`;
  return `${Math.floor(abs / 86400)}d ${suffix}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  return fmtDuration(ms / 1000);
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Number.isInteger(n)) return n.toLocaleString("en-US");
  return n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtCompact(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString("en-US");
}

/** 0.0234 -> 2.34% */
export function fmtPct(ratio: number | null | undefined, digits = 2): string {
  if (ratio == null || !Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** Score in 0..1 -> "0.83" */
export function fmtScore(score: number | null | undefined): string {
  if (score == null || !Number.isFinite(score)) return "—";
  return score.toFixed(2);
}

export function shortId(id: string | null | undefined, n = 8): string {
  if (!id) return "—";
  return id.length > n ? id.slice(0, n) : id;
}

/** Format a metric value according to its metric name. */
export function fmtMetric(metric: string, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const m = metric.toLowerCase();
  if (m.includes("rate") || m.includes("ratio") || m.includes("percent") || m.includes("saturation") || m.includes("utilization")) {
    return value <= 1.5 ? fmtPct(value) : `${fmtNum(value)}%`;
  }
  if (m.endsWith("_ms") || m.includes("latency")) return fmtMs(value);
  if (m.includes("bytes")) return fmtCompact(value);
  return fmtNum(value);
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/[_\-.]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/[_\-]+/g, " ");
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

export function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}
