"use client";

import { useMemo, useState } from "react";
import { AMBER, FAINT, hexWithAlpha } from "@/lib/colors";
import { fmtTime, parseDate } from "@/lib/format";
import type { MetricSample } from "@/lib/types";
import { useMeasure } from "@/lib/use-measure";

export interface ChartMarker {
  at: string;
  label?: string;
  color?: string;
}

export interface MetricChartProps {
  samples: MetricSample[];
  /** Healthy baseline; drawn as a dashed line with a band up to `baseline * bandRatio`. */
  baseline?: number | null;
  bandRatio?: number;
  markers?: ChartMarker[];
  height?: number;
  color?: string;
  format?: (v: number) => string;
  title?: string;
  subtitle?: string;
  className?: string;
  loading?: boolean;
}

const PAD = { top: 8, right: 8, bottom: 18, left: 44 };

/** Hand-rolled SVG line chart with a baseline band, hover crosshair and event markers. */
export function MetricChart({ samples, baseline = null, bandRatio = 1.5, markers = [], height = 120, color = AMBER, format = (v) => v.toFixed(1), title, subtitle, className = "", loading = false }: MetricChartProps) {
  const [ref, { width }] = useMeasure<HTMLDivElement>();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const points = useMemo(
    () =>
      samples
        .map((s) => ({ t: parseDate(s.at)?.getTime() ?? NaN, v: s.value }))
        .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
        .sort((a, b) => a.t - b.t),
    [samples],
  );

  const w = Math.max(0, width);
  const innerW = Math.max(1, w - PAD.left - PAD.right);
  const innerH = Math.max(1, height - PAD.top - PAD.bottom);

  const domain = useMemo(() => {
    if (points.length === 0) return null;
    const t0 = points[0].t;
    const t1 = points[points.length - 1].t;
    const values = points.map((p) => p.v);
    const upper = baseline != null ? baseline * bandRatio : null;
    let vMax = Math.max(...values, upper ?? -Infinity, baseline ?? -Infinity);
    let vMin = Math.min(...values, baseline ?? Infinity);
    if (vMin > 0.6 * vMax) vMin = vMin * 0.9;
    else vMin = Math.min(0, vMin);
    if (vMax === vMin) vMax = vMin + 1;
    vMax = vMax + (vMax - vMin) * 0.06;
    return { t0, t1: t1 === t0 ? t0 + 1 : t1, vMin, vMax };
  }, [points, baseline, bandRatio]);

  const x = (t: number) => (domain ? PAD.left + ((t - domain.t0) / (domain.t1 - domain.t0)) * innerW : 0);
  const y = (v: number) => (domain ? PAD.top + (1 - (v - domain.vMin) / (domain.vMax - domain.vMin)) * innerH : 0);

  const path = useMemo(() => {
    if (!domain || points.length === 0) return "";
    return points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, domain, innerW, innerH]);

  const yTicks = domain ? [domain.vMin, (domain.vMin + domain.vMax) / 2, domain.vMax - (domain.vMax - domain.vMin) * 0.06] : [];
  const xTicks = domain ? [0, 1 / 3, 2 / 3, 1].map((f) => domain.t0 + f * (domain.t1 - domain.t0)) : [];

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!domain || points.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const t = domain.t0 + ((px - PAD.left) / innerW) * (domain.t1 - domain.t0);
    let lo = 0;
    let hi = points.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (points[mid].t < t) lo = mid + 1;
      else hi = mid;
    }
    const cand = lo > 0 && Math.abs(points[lo - 1].t - t) < Math.abs(points[lo].t - t) ? lo - 1 : lo;
    setHoverIdx(cand);
  };

  const hover = hoverIdx != null ? points[hoverIdx] : null;
  const last = points.length > 0 ? points[points.length - 1] : null;
  const shown = hover ?? last;
  const aboveBand = shown && baseline != null ? shown.v > baseline * bandRatio : false;

  return (
    <div ref={ref} className={`w-full ${className}`}>
      {title || shown ? (
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <div className="min-w-0">
            {title ? <span className="mono text-[11.5px] text-ink">{title}</span> : null}
            {subtitle ? <span className="micro ml-2">{subtitle}</span> : null}
          </div>
          {shown ? (
            <div className="mono flex items-baseline gap-2 text-[11px]">
              <span className="text-muted">{fmtTime(new Date(shown.t).toISOString())}</span>
              <span className={`text-[13px] font-medium ${aboveBand ? "text-sev2" : "text-ink"}`}>{format(shown.v)}</span>
              {baseline != null ? <span className="text-faint">base {format(baseline)}</span> : null}
            </div>
          ) : null}
        </div>
      ) : null}
      <svg width={w} height={height} className="block overflow-visible" onMouseMove={onMove} onMouseLeave={() => setHoverIdx(null)} role="img" aria-label={title ?? "metric chart"}>
        {w > 0 && domain ? (
          <>
            {/* baseline band */}
            {baseline != null ? (
              <>
                <rect x={PAD.left} y={y(baseline * bandRatio)} width={innerW} height={Math.max(0, y(baseline) - y(baseline * bandRatio))} fill={hexWithAlpha("#3ddc97", 0.07)} />
                <line x1={PAD.left} x2={PAD.left + innerW} y1={y(baseline)} y2={y(baseline)} stroke="#3ddc97" strokeOpacity={0.55} strokeDasharray="3 3" strokeWidth={1} />
                <line x1={PAD.left} x2={PAD.left + innerW} y1={y(baseline * bandRatio)} y2={y(baseline * bandRatio)} stroke="#3ddc97" strokeOpacity={0.25} strokeDasharray="2 4" strokeWidth={1} />
              </>
            ) : null}
            {/* grid + y labels */}
            {yTicks.map((v, i) => (
              <g key={i}>
                <line x1={PAD.left} x2={PAD.left + innerW} y1={y(v)} y2={y(v)} stroke="#23282f" strokeWidth={1} />
                <text x={PAD.left - 6} y={y(v) + 3} textAnchor="end" className="fill-muted" style={{ fontSize: 9.5, fontFamily: "var(--font-mono)" }}>
                  {format(v)}
                </text>
              </g>
            ))}
            {/* x labels */}
            {xTicks.map((t, i) => (
              <text key={i} x={x(t)} y={height - 5} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"} className="fill-faint" style={{ fontSize: 9.5, fontFamily: "var(--font-mono)" }}>
                {fmtTime(new Date(t).toISOString())}
              </text>
            ))}
            {/* markers */}
            {markers.map((m, i) => {
              const t = parseDate(m.at)?.getTime();
              if (t == null || t < domain.t0 || t > domain.t1) return null;
              const c = m.color ?? AMBER;
              return (
                <g key={i}>
                  <line x1={x(t)} x2={x(t)} y1={PAD.top} y2={PAD.top + innerH} stroke={c} strokeOpacity={0.7} strokeWidth={1} strokeDasharray="1 2" />
                  <rect x={x(t) - 2} y={PAD.top - 2} width={4} height={4} fill={c} />
                  {m.label ? (
                    <text x={x(t) + 4} y={PAD.top + 8} style={{ fontSize: 9, fontFamily: "var(--font-mono)", fill: c }}>
                      {m.label}
                    </text>
                  ) : null}
                </g>
              );
            })}
            {/* series */}
            <path d={path} fill="none" stroke={color} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
            {last ? <circle cx={x(last.t)} cy={y(last.v)} r={2.2} fill={color} className={loading ? "animate-pulse-amber" : ""} /> : null}
            {/* hover crosshair */}
            {hover ? (
              <g>
                <line x1={x(hover.t)} x2={x(hover.t)} y1={PAD.top} y2={PAD.top + innerH} stroke={FAINT} strokeWidth={1} />
                <circle cx={x(hover.t)} cy={y(hover.v)} r={3} fill="#0b0d10" stroke={color} strokeWidth={1.5} />
              </g>
            ) : null}
          </>
        ) : (
          <text x={w / 2} y={height / 2} textAnchor="middle" className="fill-faint" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)" }}>
            {loading ? "loading samples…" : "no samples in window"}
          </text>
        )}
      </svg>
    </div>
  );
}
