"use client";

import Link from "next/link";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { ApprovalStatusChip, Chip, EffectChip, RiskChip, SevBadge, StatusChip, Tag } from "@/components/ui/Chip";
import { Drawer } from "@/components/ui/Drawer";
import { JsonView } from "@/components/ui/JsonView";
import { KeyValue } from "@/components/ui/KeyValue";
import { ConfidenceMeter, ScoreBreakdown } from "@/components/ui/Meter";
import { ProblemBanner } from "@/components/ui/Problem";
import { Skeleton } from "@/components/ui/Loading";
import { Countdown, RelativeTime } from "@/components/ui/Time";
import { evidenceKindColor } from "@/lib/colors";
import { fmtDateTime, humanize } from "@/lib/format";
import { useApproval } from "@/lib/hooks";

export function ApprovalDrawer({ approvalId, onClose }: { approvalId: string | null; onClose: () => void }) {
  const q = useApproval(approvalId);
  const d = q.data;

  return (
    <Drawer
      open={approvalId != null}
      onClose={onClose}
      width={680}
      kicker={d ? `${d.incident.display_id} · approval ${d.approval.status}` : "approval"}
      title={d ? d.approval.title : "Loading…"}
      footer={d && d.approval.status === "pending" ? <ApprovalActions approvalId={d.approval.id} weighty onDecided={onClose} /> : null}
    >
      {q.error ? <ProblemBanner error={q.error} onRetry={() => void q.refetch()} /> : null}
      {q.isPending ? <Skeleton rows={8} /> : null}
      {d ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/incidents/${d.incident.id}`} className="mono text-[12px] font-medium text-amber hover:underline" onClick={onClose}>
              {d.incident.display_id}
            </Link>
            <SevBadge severity={d.incident.severity} size="xs" />
            <StatusChip status={d.incident.status} size="xs" />
            <ApprovalStatusChip status={d.approval.status} size="xs" />
            <RiskChip risk={d.approval.risk} size="xs" />
            {d.approval.status === "pending" ? (
              <span className="ml-auto flex items-center gap-1 text-[10.5px] text-muted">
                expires <Countdown until={d.approval.expires_at} className="text-[11px]" />
              </span>
            ) : null}
          </div>

          <div>
            <div className="micro mb-1">Requested action</div>
            <div className="mono text-[14px] text-ink">
              {d.action_plan.tool_name}
              <span className="text-muted">(</span>
              {Object.entries(d.action_plan.arguments).map(([k, v], i) => (
                <span key={k}>
                  {i > 0 ? <span className="text-muted">, </span> : null}
                  <span className="text-muted">{k}=</span>
                  <span className="text-amber">{typeof v === "string" ? v : JSON.stringify(v)}</span>
                </span>
              ))}
              <span className="text-muted">)</span>
            </div>
            <p className="mt-1.5 text-[12px] leading-snug text-ink/90">{d.approval.summary}</p>
          </div>

          <KeyValue
            columns={2}
            items={[
              { k: "Expected impact", v: d.approval.expected_impact || "—", span: 2 },
              { k: "Rollback", v: d.approval.rollback_summary || (d.action_plan.rollback.available ? d.action_plan.rollback.tool_name : "not available"), span: 2 },
              { k: "Requested by", v: d.approval.requested_by, mono: true },
              { k: "Requested at", v: fmtDateTime(d.approval.requested_at), mono: true },
              { k: "Plan status", v: humanize(d.action_plan.status), mono: true },
              { k: "Idempotency key", v: d.action_plan.idempotency_key, mono: true },
              ...(d.approval.decided_at
                ? [
                    { k: "Decided by", v: d.approval.decided_by ?? "—", mono: true },
                    { k: "Decided at", v: fmtDateTime(d.approval.decided_at), mono: true },
                    { k: "Decision reason", v: d.approval.decision_reason || "—", span: 2 as const },
                  ]
                : []),
            ]}
          />

          {d.action_plan.policy_decision ? (
            <div className="border-t border-hairline pt-3">
              <div className="micro mb-1.5">Policy decision</div>
              <div className="flex flex-wrap items-center gap-2">
                <EffectChip effect={d.action_plan.policy_decision.effect} />
                {d.action_plan.policy_decision.matched_rule ? <Tag>{d.action_plan.policy_decision.matched_rule}</Tag> : null}
                {d.action_plan.policy_decision.invariant ? <Chip tone="danger" size="xs">{d.action_plan.policy_decision.invariant}</Chip> : null}
              </div>
              <p className="mt-1 text-[11.5px] leading-snug text-muted">{d.action_plan.policy_decision.reason}</p>
            </div>
          ) : null}

          {d.hypothesis ? (
            <div className="border-t border-hairline pt-3">
              <div className="micro mb-1.5">Hypothesis under test</div>
              <p className="text-[12.5px] leading-snug text-ink">{d.hypothesis.statement}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <Tag>{humanize(d.hypothesis.category)}</Tag>
                {d.hypothesis.suspected_root_cause_service ? <Tag accent>{d.hypothesis.suspected_root_cause_service}</Tag> : null}
                <span className="mono text-[10.5px] text-muted">{d.hypothesis.status}</span>
              </div>
              <div className="mt-3 grid gap-4 sm:grid-cols-[160px_minmax(0,1fr)]">
                <ConfidenceMeter value={d.hypothesis.confidence} />
                <ScoreBreakdown score={d.hypothesis.score} />
              </div>
              {d.hypothesis.score.explanation.length ? (
                <ul className="mt-2 space-y-0.5">
                  {d.hypothesis.score.explanation.map((l, i) => (
                    <li key={i} className="text-[11px] leading-snug text-muted">
                      {l}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {d.action_plan.verification.conditions.length ? (
            <div className="border-t border-hairline pt-3">
              <div className="micro mb-1.5">
                Will be verified by{" "}
                <span className="mono normal-case">
                  {d.action_plan.verification.require_all ? "all" : "any"} of {d.action_plan.verification.conditions.length}
                </span>
              </div>
              <ul className="space-y-1">
                {d.action_plan.verification.conditions.map((c, i) => (
                  <li key={i} className="mono text-[11.5px] text-muted">
                    <span className="text-ink">
                      {c.service}.{c.metric}
                    </span>{" "}
                    {c.description}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {d.evidence.length ? (
            <div className="border-t border-hairline pt-3">
              <div className="micro mb-1.5">Evidence cited ({d.evidence.length})</div>
              <ul className="space-y-1.5">
                {d.evidence.map((e) => (
                  <li key={e.id} className="grid grid-cols-[8px_1fr_auto] items-start gap-x-2">
                    <span className="mt-[5px] inline-block h-[7px] w-[7px] rounded-full" style={{ background: evidenceKindColor(e.kind) }} title={e.kind} />
                    <span className="min-w-0">
                      <span className="block truncate text-[12px] text-ink">{e.title}</span>
                      <span className="block text-[11px] leading-snug text-muted">{e.summary}</span>
                    </span>
                    <span className="mono shrink-0 text-[10.5px] text-faint">{e.strength.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {Object.keys(d.approval.context).length ? (
            <details className="border-t border-hairline pt-3">
              <summary className="micro cursor-pointer hover:text-ink">Approval context</summary>
              <JsonView value={d.approval.context} className="mt-2" />
            </details>
          ) : null}

          <div className="mono border-t border-hairline pt-2 text-[10px] text-faint">
            approval {d.approval.id} · plan {d.action_plan.id} · created <RelativeTime iso={d.approval.created_at} className="text-[10px]" />
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}
