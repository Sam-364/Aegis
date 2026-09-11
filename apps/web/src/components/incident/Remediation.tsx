"use client";

import { useState } from "react";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { Chip, EffectChip, PlanStatusChip, RiskChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { JsonView } from "@/components/ui/JsonView";
import { Section } from "@/components/ui/Section";
import { Countdown, RelativeTime } from "@/components/ui/Time";
import { DANGER, MUTED, OK, WARN } from "@/lib/colors";
import { fmtMetric, fmtNum, fmtPct, fmtScore, humanize, shortId } from "@/lib/format";
import type { ActionPlan, Approval, ConditionResult, VerificationCondition, VerificationResult } from "@/lib/types";

const COMPARATOR: Record<VerificationCondition["comparator"], string> = { lt: "<", lte: "≤", gt: ">", gte: "≥" };

function conditionTarget(c: VerificationCondition): string {
  if (c.target != null) return `${COMPARATOR[c.comparator]} ${fmtMetric(c.metric, c.target)}`;
  if (c.max_ratio_to_baseline != null) return `${COMPARATOR[c.comparator]} ${fmtNum(c.max_ratio_to_baseline)}× baseline`;
  return "—";
}

/** Verification conditions the workflow must prove before the incident can resolve. */
function ConditionsTable({ conditions, results }: { conditions: VerificationCondition[]; results: ConditionResult[] }) {
  const resultFor = (c: VerificationCondition) => results.find((r) => r.service === c.service && r.metric === c.metric) ?? null;
  return (
    <div className="overflow-x-auto">
      <table className="table-grid">
        <thead>
          <tr>
            <th>Service</th>
            <th>Metric</th>
            <th>Must hold</th>
            <th>Observed</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {conditions.map((c, i) => {
            const r = resultFor(c);
            return (
              <tr key={`${c.service}.${c.metric}.${i}`}>
                <td className="mono text-[11.5px]">{c.service}</td>
                <td className="mono text-[11.5px] text-muted">{c.metric}</td>
                <td className="mono text-[11.5px]">{conditionTarget(c)}</td>
                <td className="mono text-[11.5px]">{r?.value != null ? fmtMetric(c.metric, r.value) : <span className="text-faint">pending</span>}</td>
                <td>
                  {r ? (
                    <span className="flex items-center gap-2">
                      <Chip tone={r.ok ? "ok" : "danger"} dot size="xs">
                        {r.ok ? "held" : "failed"}
                      </Chip>
                      <span className="text-[11px] text-muted">{r.detail}</span>
                    </span>
                  ) : (
                    <span className="micro-mono">not checked</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Before / after metric pairs with signed deltas, keyed `${service}.${metric}`. */
export function BeforeAfter({ result }: { result: VerificationResult }) {
  const keys = Array.from(new Set([...Object.keys(result.before), ...Object.keys(result.after)])).sort();
  if (keys.length === 0) return <div className="px-3 py-2 text-[11.5px] text-faint">No metric snapshots recorded.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table-grid">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="text-right">Before</th>
            <th className="text-right">After</th>
            <th className="text-right">Δ</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const metric = k.slice(k.indexOf(".") + 1);
            const service = k.slice(0, k.indexOf("."));
            const before = result.before[k];
            const after = result.after[k];
            const has = typeof before === "number" && typeof after === "number";
            const ratio = has && before !== 0 ? (after - before) / Math.abs(before) : null;
            // Lower is better for every metric the verification engine tracks (latency, errors, saturation).
            const improved = has ? after < before : null;
            const color = ratio == null || Math.abs(ratio) < 0.02 ? MUTED : improved ? OK : DANGER;
            const width = ratio == null ? 0 : Math.min(100, Math.abs(ratio) * 100);
            return (
              <tr key={k}>
                <td>
                  <span className="mono text-[11.5px] text-ink">{service}</span>
                  <span className="mono text-[11.5px] text-muted">.{metric}</span>
                </td>
                <td className="mono text-right text-[11.5px] text-muted">{fmtMetric(metric, before)}</td>
                <td className="mono text-right text-[12px] text-ink">{fmtMetric(metric, after)}</td>
                <td className="mono text-right text-[11.5px]" style={{ color }}>
                  {ratio == null ? "—" : `${ratio > 0 ? "+" : ""}${fmtPct(ratio, 0)}`}
                </td>
                <td className="w-[120px]">
                  <div className="h-[4px] w-full overflow-hidden rounded-xs bg-panel-3">
                    <div className="h-full rounded-xs" style={{ width: `${width}%`, background: color }} />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PolicyRow({ plan }: { plan: ActionPlan }) {
  const d = plan.policy_decision;
  if (!d) return <div className="text-[11.5px] text-faint">No policy decision recorded yet.</div>;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <EffectChip effect={d.effect} />
        {d.matched_rule ? (
          <span className="text-[11.5px] text-muted">
            matched rule <span className="mono text-ink">{d.matched_rule}</span>
          </span>
        ) : null}
        {d.invariant ? (
          <span className="text-[11.5px] text-sev1">
            invariant <span className="mono">{d.invariant}</span>
          </span>
        ) : null}
      </div>
      {d.reason ? <p className="text-[11.5px] leading-snug text-ink/90">{d.reason}</p> : null}
      {d.evaluated_rules.length ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="micro mr-1">evaluated</span>
          {d.evaluated_rules.map((r) => (
            <span key={r} className={`mono text-[10.5px] ${r === d.matched_rule ? "text-amber" : "text-faint"}`}>
              {r}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** The approval console. This is the moment a human takes responsibility, so it is given weight. */
function ApprovalConsole({ plan, approval }: { plan: ActionPlan; approval: Approval | null }) {
  return (
    <div className="border-y-2 border-amber/60 bg-amber/[0.045]">
      <div className="flex flex-wrap items-center gap-2 border-b border-amber/25 px-3 py-1.5">
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" aria-hidden />
        <span className="mono text-[10.5px] tracking-[0.14em] text-amber uppercase">human decision required</span>
        {approval ? (
          <span className="ml-auto flex items-center gap-2 text-[10.5px] text-muted">
            requested <RelativeTime iso={approval.requested_at} className="text-[11px]" /> · expires <Countdown until={approval.expires_at} className="text-[11px]" />
          </span>
        ) : null}
      </div>
      <div className="grid gap-4 px-3 py-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-2.5">
          <div className="mono text-[15px] leading-snug text-ink">
            {plan.tool_name}
            <span className="text-muted">(</span>
            {Object.entries(plan.arguments).map(([k, v], i) => (
              <span key={k}>
                {i > 0 ? <span className="text-muted">, </span> : null}
                <span className="text-muted">{k}=</span>
                <span className="text-amber">{typeof v === "string" ? v : JSON.stringify(v)}</span>
              </span>
            ))}
            <span className="text-muted">)</span>
          </div>
          <dl className="space-y-1.5 text-[12px]">
            <div>
              <dt className="micro">Because</dt>
              <dd className="leading-snug text-ink/90">{plan.reason || "—"}</dd>
            </div>
            <div>
              <dt className="micro">Expected effect</dt>
              <dd className="leading-snug text-ink/90">{plan.expected_effect || "—"}</dd>
            </div>
            {approval?.hypothesis_statement ? (
              <div>
                <dt className="micro">On the hypothesis</dt>
                <dd className="leading-snug text-ink/90">
                  {approval.hypothesis_statement} <span className="mono text-amber">{fmtScore(approval.hypothesis_confidence)}</span>
                </dd>
              </div>
            ) : null}
            <div>
              <dt className="micro">If it goes wrong</dt>
              <dd className="leading-snug text-ink/90">
                {plan.rollback.available ? (
                  <>
                    rollback via <span className="mono text-ink">{plan.rollback.tool_name}</span>
                    {plan.rollback.reason ? <span className="text-muted"> — {plan.rollback.reason}</span> : null}
                  </>
                ) : (
                  <span className="text-sev2">No rollback is available for this action.</span>
                )}
              </dd>
            </div>
          </dl>
        </div>
        <div className="space-y-2.5 lg:border-l lg:border-amber/20 lg:pl-4">
          <div className="flex items-center justify-between">
            <span className="micro">Risk</span>
            <RiskChip risk={plan.risk} />
          </div>
          <div className="flex items-center justify-between">
            <span className="micro">Exactly-once key</span>
            <span className="mono text-[10.5px] text-muted" title={plan.idempotency_key}>
              {shortId(plan.idempotency_key, 12)}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="micro">Timeout</span>
            <span className="mono text-[11px] text-ink">{plan.timeout_seconds}s</span>
          </div>
          {approval ? (
            <ApprovalActions approvalId={approval.id} weighty />
          ) : (
            <div className="text-[11.5px] text-muted">The approval record has not been created yet. It appears here the moment the workflow requests it.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function PlanBlock({ plan, approval, open: initialOpen }: { plan: ActionPlan; approval: Approval | null; open: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  const awaiting = plan.status === "awaiting_approval";
  const vr = plan.verification_result;
  return (
    <div className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-3 px-3 py-2.5 text-left hover:bg-panel-2" aria-expanded={open}>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="mono text-[13px] text-ink">{plan.tool_name}</span>
            <span className="mono text-[11px] text-muted">
              {Object.entries(plan.arguments)
                .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                .join(" ")}
            </span>
            <PlanStatusChip status={plan.status} size="xs" />
            <RiskChip risk={plan.risk} size="xs" />
            {plan.attempt > 1 ? <Chip tone="warn" size="xs" mono>{`attempt ${plan.attempt}`}</Chip> : null}
            <span className="micro-mono ml-auto">{plan.proposed_by}</span>
          </span>
          <span className="mt-0.5 block truncate text-[11.5px] text-muted">{plan.reason}</span>
        </span>
        <span className="mono shrink-0 pt-0.5 text-[10px] text-faint">{open ? "−" : "+"}</span>
      </button>

      {awaiting ? <ApprovalConsole plan={plan} approval={approval} /> : null}

      {open ? (
        <div className="space-y-3 px-3 pt-1 pb-4">
          <div className="grid gap-x-5 gap-y-3 md:grid-cols-2">
            <div>
              <div className="micro mb-1">Expected effect</div>
              <p className="text-[12px] leading-snug text-ink/90">{plan.expected_effect || "—"}</p>
            </div>
            <div>
              <div className="micro mb-1">Rollback</div>
              {plan.rollback.available ? (
                <p className="text-[12px] leading-snug text-ink/90">
                  <span className="mono text-ink">{plan.rollback.tool_name}</span>{" "}
                  <span className="mono text-[11px] text-muted">
                    {Object.entries(plan.rollback.arguments)
                      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                      .join(" ")}
                  </span>
                  {plan.rollback.reason ? <span className="block text-muted">{plan.rollback.reason}</span> : null}
                </p>
              ) : (
                <p className="text-[12px] text-sev2">Not available</p>
              )}
            </div>
          </div>

          <div className="border-t border-hairline pt-2.5">
            <div className="micro mb-1.5">Policy decision</div>
            <PolicyRow plan={plan} />
          </div>

          <div className="border-t border-hairline pt-2.5">
            <div className="micro mb-1.5 flex items-center gap-2">
              Verification contract
              <span className="mono text-[10.5px] text-faint">
                {plan.verification.require_all ? "all conditions" : "any condition"} · stabilize {plan.verification.stabilization_seconds}s · timeout {plan.verification.timeout_seconds}s
              </span>
            </div>
            {plan.verification.conditions.length ? (
              <ConditionsTable conditions={plan.verification.conditions} results={vr?.condition_results ?? []} />
            ) : (
              <div className="text-[11.5px] text-faint">No conditions declared.</div>
            )}
          </div>

          <details className="border-t border-hairline pt-2.5">
            <summary className="micro cursor-pointer hover:text-ink">Raw plan</summary>
            <JsonView
              value={{ id: plan.id, idempotency_key: plan.idempotency_key, arguments: plan.arguments, agent_run_id: plan.agent_run_id, hypothesis_id: plan.hypothesis_id, tool_version: plan.tool_version }}
              className="mt-2"
            />
          </details>
        </div>
      ) : null}
    </div>
  );
}

export function Remediation({ plans, approvals }: { plans: ActionPlan[]; approvals: Approval[] }) {
  const ordered = plans.slice().sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  const approvalFor = (plan: ActionPlan) =>
    approvals.find((a) => a.id === plan.approval_id) ?? approvals.find((a) => a.action_plan_id === plan.id && a.status === "pending") ?? approvals.find((a) => a.action_plan_id === plan.id) ?? null;
  return (
    <Section title="Remediation" meta={ordered.length ? `${ordered.length} action plan${ordered.length > 1 ? "s" : ""}` : undefined} live={ordered.some((p) => p.status === "awaiting_approval")} flush>
      {ordered.length === 0 ? (
        <EmptyState
          compact
          title="No remediation proposed"
          hint="The agent may only propose. A plan appears here once a hypothesis is strong enough, and it cannot execute until the policy engine and a human have both allowed it."
        />
      ) : (
        ordered.map((p, i) => <PlanBlock key={p.id} plan={p} approval={approvalFor(p)} open={i === 0} />)
      )}
    </Section>
  );
}

export function VerificationPanel({ plans }: { plans: ActionPlan[] }) {
  const withResult = plans.filter((p) => p.verification_result != null);
  const latest = withResult.sort((a, b) => new Date(b.verification_result?.checked_at ?? 0).getTime() - new Date(a.verification_result?.checked_at ?? 0).getTime())[0];
  const vr = latest?.verification_result ?? null;
  const tone = vr?.status === "passed" ? "ok" : vr?.status === "failed" ? "danger" : vr?.status === "inconclusive" ? "warn" : "neutral";
  const held = vr?.condition_results.filter((c) => c.ok).length ?? 0;

  return (
    <Section
      title="Verification"
      meta={vr ? `${held}/${vr.condition_results.length} conditions held` : undefined}
      flush
      actions={vr ? <Chip tone={tone} dot>{vr.status}</Chip> : null}
    >
      {!vr ? (
        <EmptyState
          compact
          title="Nothing verified yet"
          hint="After a remediation executes, the verification engine re-reads the metrics and proves they recovered before the incident may be resolved."
        />
      ) : (
        <div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-hairline px-3 py-2">
            <span className="text-[12.5px] leading-snug text-ink">{vr.summary}</span>
            <span className="mono ml-auto text-[10.5px] text-faint">
              checked <RelativeTime iso={vr.checked_at} className="text-[10.5px]" /> · {latest.tool_name}
            </span>
          </div>
          <div className="border-b border-hairline">
            <div className="micro px-3 pt-2 pb-1">Before → after</div>
            <BeforeAfter result={vr} />
          </div>
          <div>
            <div className="micro px-3 pt-2 pb-1">Per-condition results</div>
            <ul className="pb-1">
              {vr.condition_results.map((c, i) => (
                <li key={`${c.service}.${c.metric}.${i}`} className="grid grid-cols-[auto_1fr_auto] items-baseline gap-x-2 px-3 py-1">
                  <span className="mono text-[10px] tracking-[0.06em] uppercase" style={{ color: c.ok ? OK : DANGER }}>
                    {c.ok ? "held" : "failed"}
                  </span>
                  <span className="min-w-0 text-[11.5px]">
                    <span className="mono text-ink">
                      {c.service}.{c.metric}
                    </span>
                    <span className="text-muted"> — {c.detail}</span>
                  </span>
                  <span className="mono text-[11px]" style={{ color: c.ok ? undefined : WARN }}>
                    {c.value != null ? fmtMetric(c.metric, c.value) : ""}
                    {c.baseline != null ? <span className="text-faint"> / base {fmtMetric(c.metric, c.baseline)}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {withResult.length > 1 ? (
            <div className="border-t border-hairline px-3 py-1.5">
              <span className="micro mr-2">earlier attempts</span>
              {withResult.slice(1).map((p) => (
                <Tag key={p.id} className="mr-1">
                  {p.tool_name} · {humanize(p.verification_result?.status ?? "")}
                </Tag>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </Section>
  );
}
