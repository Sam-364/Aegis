"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { BudgetBars, StepList } from "@/components/agent/AgentSteps";
import { Chip, RunStatusChip } from "@/components/ui/Chip";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section, Stat } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { Duration, RelativeTime } from "@/components/ui/Time";
import { IconExternal } from "@/components/shell/Icons";
import { temporalWorkflowUrl } from "@/lib/config";
import { fmtCompact, fmtMs, humanize } from "@/lib/format";
import { useAgentRun } from "@/lib/hooks";
import type { AgentStepKind } from "@/lib/types";

const KIND_ORDER: AgentStepKind[] = ["proposal", "fallback", "authorization", "tool_execution", "hypothesis_update", "remediation_plan", "decision"];

export default function AgentRunPage() {
  const params = useParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const q = useAgentRun(id);
  const run = q.data?.run;
  const steps = useMemo(() => q.data?.steps ?? [], [q.data]);

  const byKind = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of steps) counts.set(s.kind, (counts.get(s.kind) ?? 0) + 1);
    return counts;
  }, [steps]);

  const latency = useMemo(() => steps.reduce((acc, s) => acc + s.latency_ms, 0), [steps]);

  if (q.error) {
    return (
      <div className="p-5">
        <ProblemBanner error={q.error} onRetry={() => void q.refetch()} />
        <Link href="/agent-runs" className="micro mt-3 inline-block hover:text-ink">
          ← agent runs
        </Link>
      </div>
    );
  }
  if (!run) {
    return (
      <div>
        <div className="border-b border-hairline bg-panel px-5 py-4">
          <Skeleton rows={2} />
        </div>
        <Skeleton rows={8} />
      </div>
    );
  }

  const live = run.status === "running";

  return (
    <div>
      <PageHeader
        kicker={
          <span className="flex items-center gap-2">
            <Link href="/agent-runs" className="hover:text-ink">
              agent runs
            </Link>
            <span className="text-faint">/</span>
            <span className="mono normal-case">{run.id}</span>
          </span>
        }
        title={
          <>
            <span className="mono tracking-[0.1em] uppercase">{run.phase}</span>
            <RunStatusChip status={run.status} />
            {run.termination_reason ? (
              <Chip tone={run.termination_reason === "budget_exhausted" || run.termination_reason === "error" || run.termination_reason === "escalate" ? "danger" : "ok"} mono>
                {humanize(run.termination_reason)}
              </Chip>
            ) : null}
            <Duration start={run.started_at} end={run.finished_at} live={live} className="text-[16px]" />
          </>
        }
        meta={
          <>
            <Link href={`/incidents/${run.incident_id}`} className="mono text-amber hover:underline">
              incident {run.incident_id.slice(0, 8)}
            </Link>
            <Link href="/flows" className="mono hover:text-amber">
              {run.flow_name}@{run.flow_version}
            </Link>
            <span className="mono">{run.model}</span>
            {run.attempt > 1 ? <span className="mono text-sev2">attempt {run.attempt}</span> : null}
            <span className="mono text-faint">
              started <RelativeTime iso={run.started_at} className="text-[11.5px]" />
            </span>
            {run.workflow_id ? (
              <a href={temporalWorkflowUrl(run.workflow_id)} target="_blank" rel="noreferrer" className="mono flex items-center gap-1 hover:text-amber">
                workflow
                <IconExternal />
              </a>
            ) : null}
          </>
        }
      />

      {run.summary ? (
        <div className="border-b border-hairline px-5 py-2.5">
          <div className="micro mb-0.5">Summary</div>
          <p className="max-w-4xl text-[12.5px] leading-snug text-ink">{run.summary}</p>
          {run.error ? <p className="mt-1 text-[12px] text-sev1">{run.error}</p> : null}
        </div>
      ) : null}

      <div className="grid grid-cols-2 border-b border-hairline bg-panel md:grid-cols-5">
        <Stat label="Steps" value={run.steps_count} sub={KIND_ORDER.filter((k) => byKind.has(k)).map((k) => `${byKind.get(k)} ${k}`).join(" · ")} />
        <Stat label="Iterations" value={`${run.usage.iterations}/${run.budget.max_iterations}`} />
        <Stat label="Tool calls" value={`${run.usage.tool_calls}/${run.budget.max_tool_calls}`} />
        <Stat label="LLM tokens" value={fmtCompact(run.usage.llm_tokens)} sub={`${run.usage.llm_calls} calls · budget ${fmtCompact(run.budget.max_llm_tokens)}`} />
        <Stat label="Model latency" value={fmtMs(latency)} sub={`runtime ${run.usage.runtime_seconds.toFixed(1)}s`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 border-b border-hairline lg:border-r lg:border-b-0">
          <Section title="Steps" meta={`${steps.length} · in order`} live={live} flush>
            <StepList steps={steps} loading={q.isPending} defaultOpenLast={live} />
          </Section>
        </div>
        <aside>
          <Section title="Budget" meta="usage vs ceiling">
            <BudgetBars budget={run.budget} usage={run.usage} />
            <p className="mt-3 text-[11.5px] leading-snug text-muted">
              The runtime stops the loop at any ceiling and records <span className="mono">budget_exhausted</span>. Budgets are declared by the flow pack and scaled by severity.
            </p>
          </Section>
          <Section title="Identity" flush>
            <dl className="px-3 py-2 text-[11px]">
              {[
                ["run id", run.id],
                ["incident id", run.incident_id],
                ["workflow id", run.workflow_id ?? "—"],
                ["created", run.created_at],
                ["finished", run.finished_at ?? "—"],
              ].map(([k, v]) => (
                <div key={k} className="flex gap-2 border-b border-hairline py-1 last:border-b-0">
                  <dt className="micro w-[86px] shrink-0">{k}</dt>
                  <dd className="mono min-w-0 break-all text-muted">{v}</dd>
                </div>
              ))}
            </dl>
          </Section>
        </aside>
      </div>
    </div>
  );
}
