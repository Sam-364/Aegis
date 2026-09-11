"use client";

import { MetricChart, type ChartMarker } from "@/components/ui/MetricChart";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { AMBER, DANGER, INFO } from "@/lib/colors";
import { fmtMetric } from "@/lib/format";
import { useBaseline, useMetricSeries } from "@/lib/hooks";
import type { AnomalySignal } from "@/lib/types";

const WINDOW_MINUTES = 15;

interface MetricDef {
  metric: string;
  label: string;
  color: string;
}

const SERVICE_METRICS: MetricDef[] = [
  { metric: "latency_p95_ms", label: "latency p95", color: AMBER },
  { metric: "error_rate", label: "error rate", color: DANGER },
];

const INFRA_METRICS: MetricDef[] = [
  { metric: "saturation", label: "saturation", color: AMBER },
  { metric: "connections", label: "connections", color: INFO },
];

const INFRA = new Set(["postgres", "redis"]);

function OneChart({ component, def, markers }: { component: string; def: MetricDef; markers: ChartMarker[] }) {
  const series = useMetricSeries(component, def.metric, WINDOW_MINUTES, 10_000);
  const baseline = useBaseline(component, def.metric);
  if (series.error) return <ProblemBanner error={series.error} compact className="m-2" />;
  return (
    <MetricChart
      samples={series.data?.samples ?? []}
      baseline={baseline.data?.baseline ?? null}
      markers={markers}
      title={`${component}.${def.metric}`}
      subtitle={def.label}
      color={def.color}
      height={112}
      loading={series.isFetching}
      format={(v) => fmtMetric(def.metric, v)}
      className="px-3 py-2"
    />
  );
}

/**
 * Live metrics for every affected component, last 15 minutes, polled every 10s.
 * The green band is the healthy-period baseline the verification engine compares against.
 */
export function MetricsPanel({ services, signals, detectedAt, resolvedAt }: { services: string[]; signals: AnomalySignal[]; detectedAt: string; resolvedAt: string | null }) {
  const markers: ChartMarker[] = [{ at: detectedAt, label: "detected", color: DANGER }];
  if (resolvedAt) markers.push({ at: resolvedAt, label: "resolved", color: "#3ddc97" });

  if (services.length === 0) {
    return <EmptyState compact title="No affected components" hint="Metrics are charted per component named on the incident." />;
  }

  return (
    <div>
      {services.map((component) => {
        const defs = INFRA.has(component) ? INFRA_METRICS : SERVICE_METRICS;
        const componentSignals = signals.filter((s) => s.service === component);
        return (
          <section key={component} className="border-b border-hairline last:border-b-0">
            <header className="flex h-7 items-center gap-2 border-b border-hairline bg-panel px-3">
              <span className="mono text-[11px] text-ink">{component}</span>
              {componentSignals.map((s) => (
                <span key={s.id} className="mono text-[10px] text-sev2" title={s.description}>
                  {s.metric} {s.deviation_sigma.toFixed(1)}σ
                </span>
              ))}
              <span className="micro-mono ml-auto">last {WINDOW_MINUTES}m</span>
            </header>
            {defs.map((def) => (
              <OneChart key={def.metric} component={component} def={def} markers={markers} />
            ))}
          </section>
        );
      })}
    </div>
  );
}
