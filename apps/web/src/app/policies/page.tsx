"use client";

import { EffectChip, RiskChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { humanize } from "@/lib/format";
import { usePolicies } from "@/lib/hooks";
import type { PolicyRule } from "@/lib/types";

/** Every non-empty match field of a rule, rendered as `field: values`. An empty field matches everything. */
function matchFields(r: PolicyRule): { label: string; values: string[] }[] {
  const out: { label: string; values: string[] }[] = [];
  const push = (label: string, values: string[]) => {
    if (values.length > 0) out.push({ label, values: values.slice().sort() });
  };
  push("tools", r.tools);
  push("categories", r.categories);
  push("environments", r.environments);
  push("severities", r.severities);
  push("phases", r.phases);
  push("flows", r.flows);
  push("actor kinds", r.actor_kinds);
  if (r.in_agent_loop != null) out.push({ label: "in agent loop", values: [String(r.in_agent_loop)] });
  return out;
}

export default function PoliciesPage() {
  const q = usePolicies();
  const d = q.data;
  const rules = (d?.rules ?? []).slice().sort((a, b) => b.priority - a.priority || a.name.localeCompare(b.name));

  return (
    <div>
      <PageHeader
        kicker="Policy engine"
        title="What may run, and who must say yes"
        meta={
          <>
            <span className="mono">{rules.length} rules</span>
            <span className="mono">{d ? `environment ${d.environment}` : ""}</span>
            <span className="text-faint">
              Rules are evaluated in priority order; the first match decides. Rules come from <span className="mono">policies/default.yaml</span>. The invariants below are in code and cannot be
              overridden by YAML.
            </span>
          </>
        }
      />

      {q.error ? (
        <ProblemBanner error={q.error} className="m-4" onRetry={() => void q.refetch()} />
      ) : q.isPending ? (
        <Skeleton rows={8} />
      ) : !d ? (
        <EmptyState title="No policy set" />
      ) : (
        <>
          <div className="border-b border-hairline bg-sev1/[0.05] px-5 py-2.5" style={{ borderLeftWidth: 3, borderLeftColor: "#ff4d4f" }}>
            <div className="micro-mono !text-sev1">default effect</div>
            <div className="mt-0.5 flex flex-wrap items-baseline gap-2">
              <span className="mono text-[15px] tracking-[0.06em] text-sev1 uppercase">{d.default}</span>
              <span className="text-[12px] text-ink">
                A tool request that matches no rule is refused. Nothing is permitted implicitly.
              </span>
            </div>
          </div>

          <Section title="Invariants" meta={`${d.invariants.length} · not overridable`} flush>
            <div className="grid grid-cols-1 md:grid-cols-3">
              {d.invariants.map((inv) => (
                <div key={inv.name} className="border-r border-hairline px-4 py-3 last:border-r-0">
                  <div className="mono text-[11.5px] tracking-[0.04em] text-amber">{inv.name}</div>
                  <p className="mt-1 text-[11.5px] leading-snug text-ink/90">{inv.description}</p>
                </div>
              ))}
            </div>
          </Section>

          <Section title="Rules" meta="priority descending · first match wins" flush>
            {rules.length === 0 ? (
              <EmptyState compact title="No rules configured" hint="With no rules the default deny applies to everything." />
            ) : (
              <div className="overflow-x-auto">
                <table className="table-grid">
                  <thead>
                    <tr>
                      <th className="text-right">Prio</th>
                      <th>Effect</th>
                      <th>Rule</th>
                      <th>Matches</th>
                      <th>Risk window</th>
                      <th>Role</th>
                      <th>Reason given on match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((r) => {
                      const fields = matchFields(r);
                      return (
                        <tr key={r.name}>
                          <td className="mono text-right text-[12px] text-muted tabular-nums">{r.priority}</td>
                          <td>
                            <EffectChip effect={r.effect} size="xs" />
                          </td>
                          <td className="max-w-[230px]">
                            <div className="mono text-[11.5px] text-ink">{r.name}</div>
                            {r.description ? <div className="text-[11px] leading-snug text-muted">{r.description}</div> : null}
                          </td>
                          <td className="max-w-[380px]">
                            {fields.length === 0 ? (
                              <span className="mono text-[10.5px] text-faint">any request</span>
                            ) : (
                              <div className="flex flex-col gap-0.5">
                                {fields.map((f) => (
                                  <div key={f.label} className="flex flex-wrap items-baseline gap-1">
                                    <span className="micro shrink-0">{f.label}</span>
                                    {f.values.map((v) => (
                                      <Tag key={v}>{v}</Tag>
                                    ))}
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="whitespace-nowrap">
                            {r.min_risk || r.max_risk ? (
                              <span className="flex items-center gap-1">
                                {r.min_risk ? <RiskChip risk={r.min_risk} size="xs" /> : <span className="mono text-[10px] text-faint">none</span>}
                                <span className="text-faint">→</span>
                                {r.max_risk ? <RiskChip risk={r.max_risk} size="xs" /> : <span className="mono text-[10px] text-faint">any</span>}
                              </span>
                            ) : (
                              <span className="mono text-[10.5px] text-faint">—</span>
                            )}
                          </td>
                          <td>
                            {r.required_role ? (
                              <Tag accent>{humanize(r.required_role)}</Tag>
                            ) : (
                              <span className="mono text-[10.5px] text-faint">—</span>
                            )}
                          </td>
                          <td className="max-w-[260px]">
                            <span className="text-[11.5px] leading-snug text-muted">{r.reason || "—"}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section title="How a request is decided" flush>
            <ol className="space-y-1.5 px-4 py-3">
              {[
                "The authorization gate runs fourteen checks in order and stops at the first failure.",
                "The policy_effect check consults these rules in priority order; the first matching rule supplies the effect.",
                "effect allow proceeds; require_approval parks the workflow on a human signal; deny records a denial code.",
                "The three invariants are enforced in code before any rule can apply, so no YAML edit can make a dangerous tool runnable or let the agent mutate.",
              ].map((line, i) => (
                <li key={i} className="flex gap-2.5 text-[12px] leading-snug text-ink/90">
                  <span className="mono shrink-0 text-amber">{String(i + 1).padStart(2, "0")}</span>
                  <span>{line}</span>
                </li>
              ))}
            </ol>
          </Section>
        </>
      )}
    </div>
  );
}
