"use client";

import Link from "next/link";
import { PhaseDiagram } from "@/components/flows/PhasePipeline";
import { Chip, RiskChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { fmtCompact, humanize, shortId } from "@/lib/format";
import { useFlows } from "@/lib/hooks";
import type { ExitCondition, FlowPack, FlowPhase } from "@/lib/types";

function exitLabel(c: ExitCondition): string {
  if (c.value != null) return `${humanize(c.kind)} ≥ ${c.value}`;
  return humanize(c.kind);
}

function PhaseCard({ phase, flow }: { phase: FlowPhase; flow: FlowPack }) {
  return (
    <div className="min-w-0 border-r border-hairline last:border-r-0">
      <header className="flex h-7 items-center gap-2 border-b border-hairline bg-panel-2 px-2.5">
        <span className={`mono text-[11px] tracking-[0.08em] uppercase ${phase.name === flow.initial_phase ? "text-amber" : "text-ink"}`}>{phase.name}</span>
        {phase.terminal ? (
          <Chip tone="neutral" size="xs" mono>
            terminal
          </Chip>
        ) : null}
        {phase.plans_remediation ? (
          <Chip tone="live" size="xs" mono>
            plans remediation
          </Chip>
        ) : null}
        <RiskChip risk={phase.risk_ceiling} size="xs" className="ml-auto" />
      </header>
      <div className="space-y-2.5 px-2.5 py-2.5">
        <p className="text-[11.5px] leading-snug text-ink/90">{phase.objective}</p>
        <div className="mono flex flex-wrap gap-x-2.5 text-[10px] text-faint">
          <span>max {phase.max_iterations} iterations</span>
          <span>timeout {phase.timeout_seconds}s</span>
        </div>
        <div>
          <div className="micro mb-1">Allowed tools ({phase.allowed_tools.length})</div>
          {phase.allowed_tools.length === 0 ? (
            <span className="mono text-[10.5px] text-faint">none — no tool may run in this phase</span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {phase.allowed_tools
                .slice()
                .sort()
                .map((t) => (
                  <Link key={t} href="/tools">
                    <Tag className="hover:border-amber/60 hover:text-amber">{t}</Tag>
                  </Link>
                ))}
            </div>
          )}
        </div>
        <div>
          <div className="micro mb-1">Exit conditions</div>
          {phase.exit_conditions.length === 0 ? (
            <span className="mono text-[10.5px] text-faint">none</span>
          ) : (
            <ul className="space-y-0.5">
              {phase.exit_conditions.map((c, i) => (
                <li key={i} className="text-[11px] leading-snug">
                  <span className="mono text-ink">{exitLabel(c)}</span>
                  {c.description ? <span className="block text-muted">{c.description}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="micro mb-1">Transitions</div>
          <ul className="space-y-0.5">
            {phase.transitions.map((t, i) => (
              <li key={i} className="mono text-[10.5px]">
                <span className="text-muted">{t.on}</span>
                <span className="text-faint"> → </span>
                <span className="text-ink">{t.to}</span>
              </li>
            ))}
            {phase.transitions.length === 0 ? <li className="mono text-[10.5px] text-faint">none (end of flow)</li> : null}
          </ul>
        </div>
        {phase.guidance ? (
          <details>
            <summary className="micro cursor-pointer hover:text-ink">Agent guidance</summary>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">{phase.guidance}</p>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function FlowBlock({ flow }: { flow: FlowPack }) {
  const b = flow.budget;
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <span className="mono !text-[12px] !tracking-[0.04em] !normal-case text-amber">{flow.name}</span>
          <span className="mono !text-[11px] !normal-case text-muted">@{flow.version}</span>
        </span>
      }
      meta={`priority ${flow.priority}`}
      flush
      actions={
        <span className="flex items-center gap-1.5">
          {flow.applies_to
            .slice()
            .sort()
            .map((s) => (
              <Tag key={s}>{s}</Tag>
            ))}
        </span>
      }
    >
      <div className="border-b border-hairline px-4 py-2.5">
        <p className="max-w-4xl text-[12px] leading-snug text-ink/90">{flow.description}</p>
        <div className="mono mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10.5px] text-muted">
          <span>
            <span className="micro mr-1">budget</span>
            {b.max_iterations} iter · {b.max_tool_calls} tools · {b.max_llm_calls} llm · {fmtCompact(b.max_llm_tokens)} tok · {b.max_runtime_seconds}s · {b.max_remediation_attempts} attempts
          </span>
          <span>
            <span className="micro mr-1">severity factor</span>
            {Object.entries(flow.severity_budget_factor)
              .sort(([a], [c]) => a.localeCompare(c))
              .map(([k, v]) => `${k} ×${v}`)
              .join("  ")}
          </span>
          <span className="text-faint">checksum {shortId(flow.checksum, 12)}</span>
        </div>
      </div>

      <div className="border-b border-hairline px-4 py-3 graph-paper">
        <PhaseDiagram flow={flow} />
      </div>

      <div className="border-b border-hairline px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="micro">Remediation tools</span>
          {flow.remediation_tools.length === 0 ? (
            <span className="mono text-[10.5px] text-faint">none — this flow cannot mutate anything</span>
          ) : (
            flow.remediation_tools
              .slice()
              .sort()
              .map((t) => <Tag key={t}>{t}</Tag>)
          )}
          <span className="micro ml-3">risk ceiling</span>
          <RiskChip risk={flow.remediation_risk_ceiling} size="xs" />
          <span className="text-[11px] text-faint">
            Remediation runs in the workflow, never in the agent loop.
          </span>
        </div>
      </div>

      <div className={`grid grid-cols-1 md:grid-cols-2 ${flow.phases.length > 4 ? "xl:grid-cols-4" : "xl:grid-cols-3"}`}>
        {flow.phases.map((p) => (
          <PhaseCard key={p.name} phase={p} flow={flow} />
        ))}
      </div>
    </Section>
  );
}

export default function FlowsPage() {
  const q = useFlows();
  const flows = (q.data ?? []).slice().sort((a, b) => b.priority - a.priority || a.name.localeCompare(b.name));

  return (
    <div>
      <PageHeader
        kicker="Flow packs"
        title="Declarative investigation procedures"
        meta={
          <>
            <span className="mono">{flows.length} packs</span>
            <span className="text-faint">
              A flow pack is versioned YAML: which phases exist, which tools each phase may request, what must be true to leave it, and where it goes next. The runtime, not the model,
              decides the transition.
            </span>
          </>
        }
      />
      {q.error ? (
        <ProblemBanner error={q.error} className="m-4" onRetry={() => void q.refetch()} />
      ) : q.isPending ? (
        <Skeleton rows={8} />
      ) : flows.length === 0 ? (
        <EmptyState title="No flow packs registered" />
      ) : (
        flows.map((f) => <FlowBlock key={`${f.name}@${f.version}`} flow={f} />)
      )}
    </div>
  );
}
