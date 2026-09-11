"use client";

import { useState } from "react";
import Link from "next/link";
import { BudgetBars, StepList } from "@/components/agent/AgentSteps";
import { Chip, RunStatusChip } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { Duration } from "@/components/ui/Time";
import { fmtCompact, fmtMs, humanize } from "@/lib/format";
import { useAgentRunSteps } from "@/lib/hooks";
import type { AgentRun, TerminationReason } from "@/lib/types";

const TERMINATION_TONE: Record<TerminationReason, "ok" | "warn" | "danger" | "neutral" | "live"> = {
  phase_complete: "ok",
  action_planned: "ok",
  no_action_required: "ok",
  escalate: "danger",
  budget_exhausted: "danger",
  insufficient_signal: "live",
  llm_unavailable: "warn",
  incident_inactive: "neutral",
  error: "danger",
};

function RunBlock({ run, defaultOpen }: { run: AgentRun; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const live = run.status === "running";
  const steps = useAgentRunSteps(run.id, open, live);

  return (
    <div className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="w-full px-3 py-2 text-left hover:bg-panel-2" aria-expanded={open}>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`mono text-[11px] tracking-[0.1em] uppercase ${live ? "text-amber" : "text-ink"}`}>{run.phase}</span>
          <RunStatusChip status={run.status} size="xs" />
          {run.termination_reason ? (
            <Chip tone={TERMINATION_TONE[run.termination_reason] ?? "neutral"} size="xs" mono>
              {humanize(run.termination_reason)}
            </Chip>
          ) : null}
          {run.attempt > 1 ? <span className="mono text-[10px] text-faint">attempt {run.attempt}</span> : null}
          <span className="ml-auto flex items-center gap-2">
            <Duration start={run.started_at} end={run.finished_at} live={live} className="text-[10.5px]" />
            <span className="mono text-[10px] text-faint">{open ? "−" : "+"}</span>
          </span>
        </div>
        {run.summary ? <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-muted">{run.summary}</div> : null}
        <div className="mono mt-1 flex flex-wrap gap-x-2.5 text-[10px] text-faint">
          <span>{run.steps_count} steps</span>
          <span>{run.usage.iterations} iter</span>
          <span>{run.usage.tool_calls} tools</span>
          <span>{run.usage.llm_calls} llm</span>
          <span>{fmtCompact(run.usage.llm_tokens)} tok</span>
          <span className="truncate">{run.model}</span>
        </div>
        {run.error ? <div className="mt-1 text-[11px] text-sev1">{run.error}</div> : null}
      </button>
      {open ? (
        <div className="border-t border-hairline bg-canvas/40">
          <div className="px-3 py-2">
            <BudgetBars budget={run.budget} usage={run.usage} />
            <div className="mono mt-1.5 flex items-center justify-between text-[10px] text-faint">
              <span>
                {run.flow_name}@{run.flow_version}
              </span>
              <Link href={`/agent-runs/${run.id}`} className="hover:text-amber">
                open run →
              </Link>
            </div>
          </div>
          {steps.error ? <ProblemBanner error={steps.error} compact className="m-2" /> : <StepList steps={steps.data ?? []} loading={steps.isPending} defaultOpenLast={live} />}
        </div>
      ) : null}
    </div>
  );
}

export function AgentActivity({ runs }: { runs: AgentRun[] }) {
  const ordered = runs.slice().sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
  const runningIdx = ordered.findIndex((r) => r.status === "running");
  const openIdx = runningIdx >= 0 ? runningIdx : ordered.length - 1;
  const totals = ordered.reduce(
    (acc, r) => ({ tokens: acc.tokens + r.usage.llm_tokens, llm: acc.llm + r.usage.llm_calls, tools: acc.tools + r.usage.tool_calls }),
    { tokens: 0, llm: 0, tools: 0 },
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-hairline px-3">
        <div className="flex items-center gap-2">
          {runningIdx >= 0 ? <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" aria-hidden /> : null}
          <h2 className="micro !text-ink">Agent activity</h2>
          <span className="micro-mono">{ordered.length} runs</span>
        </div>
        <span className="mono text-[10px] text-faint">
          {totals.llm} llm · {fmtCompact(totals.tokens)} tok · {totals.tools} tools
        </span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {ordered.length === 0 ? (
          <EmptyState compact title="The agent has not run yet" hint="The workflow starts one agent run per flow phase. Each run is bounded by an execution budget." />
        ) : (
          ordered.map((r, i) => <RunBlock key={r.id} run={r} defaultOpen={i === openIdx} />)
        )}
      </div>
    </div>
  );
}

/** Compact per-run row used on the /agent-runs index. */
export function RunUsageCells({ run }: { run: AgentRun }) {
  return (
    <>
      <td className="mono text-right text-[11px] text-muted">{run.usage.iterations}</td>
      <td className="mono text-right text-[11px] text-muted">{run.usage.tool_calls}</td>
      <td className="mono text-right text-[11px] text-muted">{run.usage.llm_calls}</td>
      <td className="mono text-right text-[11px] text-muted">{fmtCompact(run.usage.llm_tokens)}</td>
      <td className="mono text-right text-[11px] text-muted">{fmtMs(run.usage.runtime_seconds * 1000)}</td>
    </>
  );
}
