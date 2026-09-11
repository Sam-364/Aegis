"use client";

import Link from "next/link";
import { SevBadge, StatusChip, Tag } from "@/components/ui/Chip";
import { Duration } from "@/components/ui/Time";
import { EmptyState } from "@/components/ui/EmptyState";
import { isActiveStatus } from "@/lib/colors";
import type { IncidentSummary } from "@/lib/types";

export function IncidentTable({ incidents, compact = false, emptyTitle = "No incidents", emptyHint }: { incidents: IncidentSummary[]; compact?: boolean; emptyTitle?: string; emptyHint?: string }) {
  if (incidents.length === 0) return <EmptyState compact title={emptyTitle} hint={emptyHint} />;
  return (
    <div className="overflow-x-auto">
      <table className="table-grid">
        <thead>
          <tr>
            <th>Incident</th>
            <th>Sev</th>
            <th>Status</th>
            <th>Title</th>
            <th className="text-right">Duration</th>
            <th>Affected services</th>
            {!compact ? <th>Flow</th> : null}
            {!compact ? <th className="text-right">Signals</th> : null}
          </tr>
        </thead>
        <tbody>
          {incidents.map((inc) => {
            const active = isActiveStatus(inc.status);
            return (
              <tr key={inc.id} className="group">
                <td>
                  <Link href={`/incidents/${inc.id}`} className="mono text-[12px] font-medium text-amber hover:underline">
                    {inc.display_id}
                  </Link>
                </td>
                <td>
                  <SevBadge severity={inc.severity} />
                </td>
                <td>
                  <StatusChip status={inc.status} />
                </td>
                <td className="max-w-[420px]">
                  <Link href={`/incidents/${inc.id}`} className="block truncate text-[12.5px] text-ink group-hover:text-amber">
                    {inc.title}
                  </Link>
                  {!compact && inc.root_cause_summary ? <div className="truncate text-[11px] text-muted">{inc.root_cause_summary}</div> : null}
                </td>
                <td className="text-right">
                  <Duration start={inc.detected_at} end={inc.resolved_at} live={active} fallbackSeconds={inc.duration_seconds} className="text-[12px]" />
                </td>
                <td>
                  <div className="flex max-w-[360px] flex-wrap gap-1">
                    {inc.affected_services.slice(0, compact ? 4 : 8).map((s) => (
                      <Tag key={s}>{s}</Tag>
                    ))}
                    {inc.affected_services.length > (compact ? 4 : 8) ? <span className="mono text-[10.5px] text-muted">+{inc.affected_services.length - (compact ? 4 : 8)}</span> : null}
                  </div>
                </td>
                {!compact ? <td className="mono text-[11px] text-muted">{inc.flow_name ?? "—"}</td> : null}
                {!compact ? <td className="mono text-right text-[11px] text-muted">{inc.signal_count}</td> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
