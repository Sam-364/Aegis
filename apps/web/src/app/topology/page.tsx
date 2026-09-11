"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ServiceMap, faultTargets } from "@/components/topology/ServiceMap";
import { Chip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { KeyValue, type KV } from "@/components/ui/KeyValue";
import { LiveDot } from "@/components/ui/LiveDot";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { healthColor, saturationColor } from "@/lib/colors";
import { fmtDuration, fmtMetric, fmtMs, fmtPct, humanize } from "@/lib/format";
import { useIncidents, useTopology } from "@/lib/hooks";
import { isServiceState, type TopologyNode } from "@/lib/types";

function nodeDetails(node: TopologyNode): KV[] {
  const base: KV[] = [
    { k: "Kind", v: node.kind, mono: true },
    { k: "Tier", v: String(node.tier), mono: true },
    { k: "Owner", v: node.owner, mono: true },
    { k: "Version", v: node.version, mono: true },
    { k: "Replicas", v: String(node.replicas), mono: true },
  ];
  if (isServiceState(node.state)) {
    return [
      ...base,
      { k: "Health", v: <span style={{ color: healthColor(node.state.health) }}>{node.state.health}</span>, mono: true },
      { k: "Up", v: node.state.up ? "yes" : "no", mono: true },
      { k: "Latency p95", v: fmtMs(node.state.latency_p95_ms), mono: true },
      { k: "Error rate", v: fmtPct(node.state.error_rate), mono: true },
    ];
  }
  return [
    ...base,
    { k: "Up", v: node.state.up ? "yes" : "no", mono: true },
    { k: "Connections", v: String(node.state.connections), mono: true },
    { k: "Saturation", v: <span style={{ color: saturationColor(node.state.saturation) }}>{fmtPct(node.state.saturation, 0)}</span>, mono: true },
  ];
}

export default function TopologyPage() {
  const topo = useTopology(5_000);
  const active = useIncidents({ active: true, limit: 100 }, { refetchInterval: 10_000 });
  const [selected, setSelected] = useState<string | null>(null);

  const highlight = useMemo(() => new Set((active.data?.items ?? []).flatMap((i) => i.affected_services)), [active.data]);
  const faults = topo.data?.active_faults.filter((f) => f.active) ?? [];
  const faulted = useMemo(() => faultTargets(topo.data?.active_faults ?? []), [topo.data?.active_faults]);
  const node = topo.data?.nodes.find((n) => n.name === selected) ?? null;
  const unhealthy = (topo.data?.nodes ?? []).filter((n) => !n.state.up || (isServiceState(n.state) ? n.state.health !== "healthy" : n.state.saturation >= 0.6));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        kicker="Topology"
        title="Simulated service map"
        meta={
          <>
            <span className="mono">{topo.data ? `${topo.data.nodes.length} components · ${topo.data.edges.length} edges` : "…"}</span>
            {topo.data ? (
              <span className="mono flex items-center gap-1.5">
                <LiveDot tone={faults.length > 0 ? "danger" : "ok"} pulse />
                {faults.length > 0 ? `${faults.length} active fault${faults.length > 1 ? "s" : ""}` : "no injected faults"}
              </span>
            ) : null}
            <span className="mono text-faint">{topo.data ? `sim time ${topo.data.time}` : ""}</span>
            <span className="text-faint">polled every 5s · tiers left to right</span>
          </>
        }
        actions={
          <Link href="/simulation" className="micro rounded-xs border border-hairline-2 px-2 py-1 hover:border-muted hover:text-ink">
            inject a fault →
          </Link>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-h-0 overflow-auto border-b border-hairline p-4 graph-paper xl:border-r xl:border-b-0">
          {topo.error ? (
            <ProblemBanner error={topo.error} onRetry={() => void topo.refetch()} />
          ) : topo.data ? (
            <ServiceMap topology={topo.data} highlight={highlight} selected={selected} onSelect={setSelected} />
          ) : (
            <Skeleton rows={8} />
          )}
          {topo.data ? (
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-hairline pt-2">
              {(["healthy", "degraded", "unhealthy", "unknown"] as const).map((h) => (
                <span key={h} className="mono flex items-center gap-1.5 text-[10px] text-muted">
                  <span className="inline-block h-2.5 w-[3px]" style={{ background: healthColor(h) }} /> {h}
                </span>
              ))}
              <span className="mono flex items-center gap-1.5 text-[10px] text-muted">
                <span className="inline-block h-2.5 w-2.5 border border-amber" /> in an active incident
              </span>
              <span className="mono flex items-center gap-1.5 text-[10px] text-muted">
                <span className="inline-block h-px w-4 bg-faint" /> non-critical edge
              </span>
              <span className="mono flex items-center gap-1.5 text-[10px] text-muted">
                <span className="inline-block h-px w-4 bg-muted" /> critical edge
              </span>
            </div>
          ) : null}
        </div>

        <aside className="min-h-0 overflow-y-auto">
          <Section title="Active faults" meta={`${faults.length}`} live={faults.length > 0} flush>
            {faults.length === 0 ? (
              <EmptyState compact title="No faults injected" hint="The simulator is running clean. Inject a scenario to exercise the runtime." />
            ) : (
              <ul>
                {faults.map((f) => (
                  <li key={f.id} className="border-b border-hairline px-3 py-2 last:border-b-0">
                    <div className="flex items-center gap-2">
                      <span className="mono text-[11.5px] text-sev1">{f.scenario_id}</span>
                      <Chip tone="danger" size="xs" mono>
                        {f.fault_type}
                      </Chip>
                      <span className="mono ml-auto text-[10px] text-faint">{f.id}</span>
                    </div>
                    <div className="mono mt-1 flex flex-wrap gap-x-2 text-[10.5px] text-muted">
                      {Object.entries(f.params).map(([k, v]) => (
                        <span key={k}>
                          <span className="text-faint">{k}=</span>
                          {typeof v === "string" ? v : JSON.stringify(v)}
                        </span>
                      ))}
                    </div>
                    <div className="mono mt-0.5 text-[10px] text-faint">
                      started {new Date(f.started_at * 1000).toLocaleTimeString()} {f.duration_seconds != null ? `· lasts ${fmtDuration(f.duration_seconds)}` : "· until cleared"}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Degraded components" meta={`${unhealthy.length}`} flush>
            {unhealthy.length === 0 ? (
              <EmptyState compact title="All components healthy" />
            ) : (
              <ul>
                {unhealthy.map((n) => (
                  <li key={n.name} className="flex items-center gap-2 border-b border-hairline px-3 py-1.5 last:border-b-0">
                    <LiveDot color={isServiceState(n.state) ? healthColor(n.state.health) : saturationColor(n.state.saturation)} />
                    <button type="button" onClick={() => setSelected(n.name)} className="mono text-[11.5px] text-ink hover:text-amber">
                      {n.name}
                    </button>
                    {faulted.has(n.name) ? (
                      <Chip tone="danger" size="xs" mono>
                        fault
                      </Chip>
                    ) : null}
                    <span className="mono ml-auto text-[10.5px] text-muted">
                      {isServiceState(n.state) ? `${fmtMs(n.state.latency_p95_ms)} · ${fmtPct(n.state.error_rate)}` : `sat ${fmtPct(n.state.saturation, 0)}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {node ? (
            <Section title={node.name} meta={humanize(node.kind)} flush actions={<button type="button" onClick={() => setSelected(null)} className="micro hover:text-ink">clear</button>}>
              <div className="px-3 py-3">
                <KeyValue columns={2} dense items={nodeDetails(node)} />
                {highlight.has(node.name) ? (
                  <div className="mt-3 border-t border-hairline pt-2">
                    <div className="micro mb-1">In active incidents</div>
                    <div className="flex flex-wrap gap-1">
                      {(active.data?.items ?? [])
                        .filter((i) => i.affected_services.includes(node.name))
                        .map((i) => (
                          <Link key={i.id} href={`/incidents/${i.id}`}>
                            <Tag accent>{i.display_id}</Tag>
                          </Link>
                        ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </Section>
          ) : (
            <Section title="Component" flush>
              <EmptyState compact title="Nothing selected" hint="Click a component on the map to inspect its live state." />
            </Section>
          )}

          <Section title="All components" meta={`${topo.data?.nodes.length ?? 0}`} flush>
            <div className="overflow-x-auto">
              <table className="table-grid">
                <thead>
                  <tr>
                    <th>Component</th>
                    <th>Tier</th>
                    <th className="text-right">Key metric</th>
                  </tr>
                </thead>
                <tbody>
                  {(topo.data?.nodes ?? [])
                    .slice()
                    .sort((a, b) => a.tier - b.tier || a.name.localeCompare(b.name))
                    .map((n) => (
                      <tr key={n.name} onClick={() => setSelected(n.name)} className="cursor-pointer">
                        <td>
                          <span className="flex items-center gap-1.5">
                            <LiveDot color={isServiceState(n.state) ? healthColor(n.state.health) : saturationColor(n.state.saturation)} size={5} />
                            <span className="mono text-[11.5px]">{n.name}</span>
                          </span>
                        </td>
                        <td className="mono text-[11px] text-muted">{n.tier}</td>
                        <td className="mono text-right text-[11px] text-muted">
                          {isServiceState(n.state) ? fmtMetric("latency_p95_ms", n.state.latency_p95_ms) : fmtMetric("saturation", n.state.saturation)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </Section>
        </aside>
      </div>
    </div>
  );
}
