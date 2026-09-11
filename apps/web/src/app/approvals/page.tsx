"use client";

import Link from "next/link";
import { useState } from "react";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { ApprovalDrawer } from "@/components/approvals/ApprovalDrawer";
import { useIncidentIndex } from "@/components/overview/Widgets";
import { ApprovalStatusChip, RiskChip, SevBadge, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { Meter } from "@/components/ui/Meter";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { Tabs, type TabDef } from "@/components/ui/Tabs";
import { Countdown, RelativeTime } from "@/components/ui/Time";
import { fmtScore } from "@/lib/format";
import { useApprovals } from "@/lib/hooks";
import type { ApprovalStatus } from "@/lib/types";

const HISTORY: ApprovalStatus[] = ["approved", "rejected", "expired", "cancelled"];

export default function ApprovalsPage() {
  const [historyStatus, setHistoryStatus] = useState<ApprovalStatus>("approved");
  const [open, setOpen] = useState<string | null>(null);
  const pending = useApprovals("pending");
  const history = useApprovals(historyStatus, { refetchInterval: 60_000 });
  const index = useIncidentIndex();

  const tabs: TabDef<ApprovalStatus>[] = HISTORY.map((s) => ({ id: s, label: s, count: s === historyStatus ? (history.data?.length ?? null) : null }));
  const queue = pending.data ?? [];

  return (
    <div>
      <PageHeader
        kicker="Approvals"
        title="Human decision queue"
        meta={
          <>
            <span className="mono">{queue.length} awaiting a decision</span>
            <span className="text-faint">
              A remediation that matches a <span className="mono">require_approval</span> rule cannot execute until a human with the operator role decides.
            </span>
          </>
        }
      />

      <Section title="Pending" meta={`${queue.length}`} live={queue.length > 0} flush>
        {pending.error ? (
          <ProblemBanner error={pending.error} className="m-3" onRetry={() => void pending.refetch()} />
        ) : pending.isPending ? (
          <Skeleton rows={4} />
        ) : queue.length === 0 ? (
          <EmptyState title="Nothing awaiting a human" hint="When the policy engine returns require_approval, the workflow parks on a signal and the request appears here." />
        ) : (
          <ul>
            {queue.map((a) => {
              const inc = index.get(a.incident_id);
              return (
                <li key={a.id} className="border-b border-hairline last:border-b-0 animate-slide-in">
                  <div className="grid gap-4 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_320px]">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link href={`/incidents/${a.incident_id}`} className="mono text-[12px] font-medium text-amber hover:underline">
                          {inc?.display_id ?? a.incident_id.slice(0, 8)}
                        </Link>
                        {inc ? <SevBadge severity={inc.severity} size="xs" /> : null}
                        <RiskChip risk={a.risk} size="xs" />
                        <button type="button" onClick={() => setOpen(a.id)} className="micro hover:text-ink">
                          full context →
                        </button>
                        <span className="ml-auto flex items-center gap-1 text-[10.5px] text-muted">
                          requested <RelativeTime iso={a.requested_at} className="text-[11px]" /> · expires <Countdown until={a.expires_at} className="text-[11px]" />
                        </span>
                      </div>
                      <div className="mono mt-1.5 text-[14px] text-ink">{a.title}</div>
                      <p className="mt-1 text-[12px] leading-snug text-muted">{a.summary}</p>
                      <div className="mt-2 border-l-2 border-hairline-2 pl-2.5">
                        <div className="micro mb-0.5">Hypothesis</div>
                        <p className="text-[12px] leading-snug text-ink/90">{a.hypothesis_statement}</p>
                        <Meter value={a.hypothesis_confidence} label={`confidence ${fmtScore(a.hypothesis_confidence)}`} showValue={false} height={4} className="mt-1 max-w-[320px]" />
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-muted">
                        <span>
                          <span className="micro mr-1">impact</span>
                          {a.expected_impact || "—"}
                        </span>
                        <span>
                          <span className="micro mr-1">rollback</span>
                          {a.rollback_summary || "—"}
                        </span>
                        {a.evidence_ids.length ? <Tag>{a.evidence_ids.length} evidence</Tag> : null}
                      </div>
                    </div>
                    <div className="lg:border-l lg:border-hairline lg:pl-4">
                      <ApprovalActions approvalId={a.id} weighty />
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title="History" meta={history.data ? `${history.data.length} ${historyStatus}` : undefined} flush>
        <Tabs tabs={tabs} value={historyStatus} onChange={setHistoryStatus} />
        {history.error ? (
          <ProblemBanner error={history.error} className="m-3" />
        ) : history.isPending ? (
          <Skeleton rows={4} />
        ) : (history.data ?? []).length === 0 ? (
          <EmptyState compact title={`No ${historyStatus} approvals`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-grid">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Action</th>
                  <th>Risk</th>
                  <th>Status</th>
                  <th>Decided by</th>
                  <th>Reason</th>
                  <th>Decided</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(history.data ?? []).map((a) => {
                  const inc = index.get(a.incident_id);
                  return (
                    <tr key={a.id}>
                      <td>
                        <Link href={`/incidents/${a.incident_id}`} className="mono text-[11.5px] text-amber hover:underline">
                          {inc?.display_id ?? a.incident_id.slice(0, 8)}
                        </Link>
                      </td>
                      <td className="mono max-w-[280px] truncate text-[11.5px] text-ink">{a.title}</td>
                      <td>
                        <RiskChip risk={a.risk} size="xs" />
                      </td>
                      <td>
                        <ApprovalStatusChip status={a.status} size="xs" />
                      </td>
                      <td className="mono text-[11px] text-muted">{a.decided_by ?? "—"}</td>
                      <td className="max-w-[240px] truncate text-[11.5px] text-muted">{a.decision_reason || "—"}</td>
                      <td>
                        <RelativeTime iso={a.decided_at} className="text-[11px] text-muted" />
                      </td>
                      <td>
                        <button type="button" onClick={() => setOpen(a.id)} className="micro hover:text-ink">
                          open
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <ApprovalDrawer approvalId={open} onClose={() => setOpen(null)} />
    </div>
  );
}
