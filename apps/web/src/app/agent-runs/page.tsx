"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useIncidentIndex } from "@/components/overview/Widgets";
import { Chip, RunStatusChip } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Stat } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { Duration, RelativeTime } from "@/components/ui/Time";
import { fmtCompact, humanize } from "@/lib/format";
import { useAgentRuns } from "@/lib/hooks";

export default function AgentRunsPage() {
  const runs = useAgentRuns(100);
  const index = useIncidentIndex();
  const list = useMemo(() => runs.data ?? [], [runs.data]);

  const totals = useMemo(
    () =>
      list.reduce(
        (acc, r) => ({
          tokens: acc.tokens + r.usage.llm_tokens,
          llm: acc.llm + r.usage.llm_calls,
          tools: acc.tools + r.usage.tool_calls,
          running: acc.running + (r.status === "running" ? 1 : 0),
          exhausted: acc.exhausted + (r.status === "budget_exhausted" ? 1 : 0),
        }),
        { tokens: 0, llm: 0, tools: 0, running: 0, exhausted: 0 },
      ),
    [list],
  );

  return (
    <div>
      <PageHeader
        kicker="Agent runs"
        title="Reasoning loop executions"
        meta={
          <>
            <span className="mono">{list.length} recent runs</span>
            <span className="text-faint">One run per flow phase. Each is bounded by an execution budget and can only request read-only tools.</span>
          </>
        }
      />
      <div className="grid grid-cols-2 border-b border-hairline bg-panel md:grid-cols-5">
        <Stat label="Runs" value={list.length} sub="most recent first" />
        <Stat label="Running" value={totals.running} tone={totals.running > 0 ? "#f5b31a" : undefined} sub="in the loop now" />
        <Stat label="LLM calls" value={totals.llm} sub={`${fmtCompact(totals.tokens)} tokens`} />
        <Stat label="Tool calls" value={totals.tools} sub="read-only inside the loop" />
        <Stat label="Budget exhausted" value={totals.exhausted} tone={totals.exhausted > 0 ? "#ff4d4f" : undefined} sub="stopped by the runtime" />
      </div>

      {runs.error ? (
        <ProblemBanner error={runs.error} className="m-4" onRetry={() => void runs.refetch()} />
      ) : runs.isPending ? (
        <Skeleton rows={8} />
      ) : list.length === 0 ? (
        <EmptyState title="No agent runs yet" hint="Runs appear once an incident enters a flow phase." />
      ) : (
        <div className="overflow-x-auto">
          <table className="table-grid">
            <thead>
              <tr>
                <th>Run</th>
                <th>Incident</th>
                <th>Phase</th>
                <th>Status</th>
                <th>Termination</th>
                <th className="text-right">Duration</th>
                <th className="text-right">Steps</th>
                <th className="text-right">Iter</th>
                <th className="text-right">Tools</th>
                <th className="text-right">LLM</th>
                <th className="text-right">Tokens</th>
                <th>Model</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => {
                const inc = index.get(r.incident_id);
                return (
                  <tr key={r.id}>
                    <td>
                      <Link href={`/agent-runs/${r.id}`} className="mono text-[11.5px] text-amber hover:underline">
                        {r.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td>
                      <Link href={`/incidents/${r.incident_id}`} className="mono text-[11.5px] text-ink hover:text-amber">
                        {inc?.display_id ?? r.incident_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="mono text-[11px] tracking-[0.06em] text-muted uppercase">{r.phase}</td>
                    <td>
                      <RunStatusChip status={r.status} size="xs" />
                    </td>
                    <td>
                      {r.termination_reason ? (
                        <Chip
                          tone={r.termination_reason === "budget_exhausted" || r.termination_reason === "error" || r.termination_reason === "escalate" ? "danger" : r.termination_reason === "llm_unavailable" ? "warn" : "ok"}
                          size="xs"
                          mono
                        >
                          {humanize(r.termination_reason)}
                        </Chip>
                      ) : (
                        <span className="mono text-[10.5px] text-faint">—</span>
                      )}
                    </td>
                    <td className="text-right">
                      <Duration start={r.started_at} end={r.finished_at} live={r.status === "running"} className="text-[11px]" />
                    </td>
                    <td className="mono text-right text-[11px] text-muted">{r.steps_count}</td>
                    <td className="mono text-right text-[11px] text-muted">
                      {r.usage.iterations}
                      <span className="text-faint">/{r.budget.max_iterations}</span>
                    </td>
                    <td className="mono text-right text-[11px] text-muted">{r.usage.tool_calls}</td>
                    <td className="mono text-right text-[11px] text-muted">{r.usage.llm_calls}</td>
                    <td className="mono text-right text-[11px] text-muted">{fmtCompact(r.usage.llm_tokens)}</td>
                    <td className="mono max-w-[140px] truncate text-[11px] text-muted">{r.model}</td>
                    <td>
                      <RelativeTime iso={r.started_at} className="text-[11px] text-muted" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
