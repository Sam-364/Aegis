"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useGlobalStream } from "@/components/shell/GlobalStream";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { ServiceMap } from "@/components/topology/ServiceMap";
import { RiskChip, SevBadge, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { LiveDot } from "@/components/ui/LiveDot";
import { Meter } from "@/components/ui/Meter";
import { ProblemBanner } from "@/components/ui/Problem";
import { Section, Stat } from "@/components/ui/Section";
import { Countdown, RelativeTime } from "@/components/ui/Time";
import { TONE_COLOR } from "@/lib/colors";
import { eventSummary, eventTone } from "@/lib/events";
import { fmtDuration, fmtTime, humanize } from "@/lib/format";
import { useApprovals, useIncidentStats, useIncidents, useReady, useTopology } from "@/lib/hooks";
import type { IncidentSummary } from "@/lib/types";

export function useIncidentIndex(): Map<string, IncidentSummary> {
  const all = useIncidents({ limit: 200 }, { refetchInterval: 30_000 });
  return useMemo(() => new Map((all.data?.items ?? []).map((i) => [i.id, i])), [all.data]);
}

export function StatsStrip() {
  const stats = useIncidentStats();
  const s = stats.data;
  return (
    <div className="grid grid-cols-3 border-b border-hairline bg-panel md:grid-cols-6">
      <Stat label="Active" value={s?.active ?? "—"} tone={s && s.active > 0 ? "#f5b31a" : undefined} sub={s ? `${s.total} total` : undefined} />
      <Stat label="Pending approvals" value={s?.pending_approvals ?? "—"} tone={s && s.pending_approvals > 0 ? "#f5b31a" : undefined} sub="human decisions" />
      <Stat label="Resolved" value={s?.resolved ?? "—"} tone={s && s.resolved > 0 ? "#3ddc97" : undefined} sub="by the runtime" />
      <Stat label="MTTR" value={s ? fmtDuration(s.mttr_seconds) : "—"} sub="detect → resolve" />
      <Stat label="Memories" value={s?.memories ?? "—"} sub="learned incidents" />
      <Stat
        label="By status"
        mono={false}
        value={
          <span className="flex flex-wrap gap-x-2 gap-y-0.5 pt-0.5">
            {s
              ? Object.entries(s.by_status)
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => (
                    <span key={k} className="mono text-[11px] text-muted">
                      <span className="text-ink">{n}</span> {humanize(k)}
                    </span>
                  ))
              : "—"}
          </span>
        }
      />
    </div>
  );
}

export function ReadinessStrip() {
  const ready = useReady();
  const checks = ready.data?.checks ?? {};
  const order = ["database", "redis", "temporal", "simulator"];
  const keys = [...order.filter((k) => k in checks), ...Object.keys(checks).filter((k) => !order.includes(k))];
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-hairline px-4 py-1.5">
      <span className="micro">Readiness</span>
      {ready.isError ? (
        <span className="mono flex items-center gap-1.5 text-[11px] text-sev1">
          <LiveDot tone="danger" /> control plane unreachable
        </span>
      ) : keys.length === 0 ? (
        <span className="mono text-[11px] text-muted">…</span>
      ) : (
        keys.map((k) => {
          const c = checks[k];
          const extra = Object.entries(c)
            .filter(([kk]) => kk !== "ok")
            .map(([kk, v]) => `${kk}=${String(v)}`)
            .join(" ");
          return (
            <span key={k} className="mono flex items-center gap-1.5 text-[11px]" title={extra || undefined}>
              <LiveDot tone={c.ok ? "ok" : "danger"} />
              <span className={c.ok ? "text-ink" : "text-sev1"}>{k}</span>
              {extra ? <span className="hidden text-faint xl:inline">{extra}</span> : null}
            </span>
          );
        })
      )}
      <span className="mono ml-auto text-[10.5px] text-faint">{ready.data ? `status ${ready.data.status}` : ""}</span>
    </div>
  );
}

export function PendingApprovalsRail() {
  const approvals = useApprovals("pending");
  const index = useIncidentIndex();
  const list = approvals.data ?? [];
  return (
    <Section title="Pending approvals" meta={`${list.length}`} live={list.length > 0} flush actions={<Link href="/approvals" className="micro hover:text-ink">queue →</Link>}>
      {approvals.error ? <ProblemBanner error={approvals.error} compact className="m-3" /> : null}
      {list.length === 0 && !approvals.error ? (
        <EmptyState compact title="Nothing awaiting a human" hint="Remediations that match a require_approval rule will queue here." />
      ) : (
        <ul>
          {list.map((a) => {
            const inc = index.get(a.incident_id);
            return (
              <li key={a.id} className="border-b border-hairline px-3 py-3 last:border-b-0 animate-slide-in">
                <div className="flex items-center gap-2">
                  <Link href={`/incidents/${a.incident_id}`} className="mono text-[11.5px] font-medium text-amber hover:underline">
                    {inc?.display_id ?? a.incident_id.slice(0, 8)}
                  </Link>
                  {inc ? <SevBadge severity={inc.severity} size="xs" /> : null}
                  <RiskChip risk={a.risk} size="xs" />
                  <span className="ml-auto flex items-center gap-1 text-[10.5px] text-muted">
                    expires <Countdown until={a.expires_at} className="text-[11px]" />
                  </span>
                </div>
                <div className="mono mt-1.5 text-[12.5px] text-ink">{a.title}</div>
                <div className="mt-1 line-clamp-2 text-[11.5px] leading-snug text-muted">{a.hypothesis_statement}</div>
                <Meter value={a.hypothesis_confidence} label="confidence" className="mt-1.5" height={4} />
                <div className="mt-2">
                  <ApprovalActions approvalId={a.id} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

export function EventTicker({ limit = 200 }: { limit?: number }) {
  const { events, status } = useGlobalStream();
  const index = useIncidentIndex();
  const rows = events.slice(0, limit);
  return (
    <Section title="Live event ticker" meta={`${rows.length} · newest first`} live={status === "open"} flush bodyClassName="max-h-[480px] overflow-y-auto">
      {rows.length === 0 ? (
        <EmptyState compact title={status === "open" ? "Listening on /api/v1/events/stream" : `Stream ${status}`} hint="Every incident event the runtime emits appears here the moment it is recorded." />
      ) : (
        <ol>
          {rows.map((ev) => {
            const inc = index.get(ev.incident_id);
            const tone = TONE_COLOR[eventTone(ev.type) === "amber" ? "live" : (eventTone(ev.type) as keyof typeof TONE_COLOR)];
            const summary = eventSummary(ev);
            return (
              <li key={ev.id} className="grid grid-cols-[60px_84px_1fr] items-baseline gap-x-3 border-b border-hairline px-3 py-1.5 last:border-b-0 animate-slide-in hover:bg-panel-2">
                <span className="mono text-[11px] text-muted">{fmtTime(ev.at)}</span>
                <Link href={`/incidents/${ev.incident_id}`} className="mono truncate text-[11px] text-amber hover:underline">
                  {inc?.display_id ?? ev.incident_id.slice(0, 8)}
                </Link>
                <span className="min-w-0">
                  <span className="block truncate text-[12px] text-ink">{ev.title}</span>
                  <span className="flex gap-2 text-[10.5px]">
                    <span className="mono" style={{ color: tone }}>
                      {ev.type}
                    </span>
                    {summary ? <span className="truncate text-muted">{summary}</span> : null}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

export function TopologyMiniMap({ highlight }: { highlight: ReadonlySet<string> }) {
  const topo = useTopology(5_000);
  return (
    <Section title="Topology" meta={topo.data ? `${topo.data.nodes.length} components · ${topo.data.active_faults.length} faults` : undefined} actions={<Link href="/topology" className="micro hover:text-ink">full map →</Link>}>
      {topo.error ? <ProblemBanner error={topo.error} compact /> : topo.data ? <ServiceMap topology={topo.data} mini highlight={highlight} /> : <div className="h-24" />}
      {highlight.size > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <span className="micro mr-1">in active incidents</span>
          {Array.from(highlight).map((s) => (
            <Tag key={s} accent>
              {s}
            </Tag>
          ))}
        </div>
      ) : null}
    </Section>
  );
}

export function RecentActivityHint({ iso }: { iso: string | null }) {
  return iso ? <RelativeTime iso={iso} className="text-[11px] text-muted" /> : null;
}
