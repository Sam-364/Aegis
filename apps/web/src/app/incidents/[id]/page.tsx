"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { AgentActivity } from "@/components/incident/AgentActivity";
import { AuditPanel } from "@/components/incident/AuditPanel";
import { EvidenceGraph } from "@/components/incident/EvidenceGraph";
import { HumanActions } from "@/components/incident/HumanActions";
import { LiveLedgerPanel } from "@/components/incident/LiveLedger";
import { MetricsPanel } from "@/components/incident/MetricsPanel";
import { Remediation, VerificationPanel } from "@/components/incident/Remediation";
import { RootCause } from "@/components/incident/RootCause";
import { Chip, ExecStatusChip, SevBadge, StatusChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { GateSequence } from "@/components/ui/GateSequence";
import { JsonView } from "@/components/ui/JsonView";
import { ProblemBanner } from "@/components/ui/Problem";
import { Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { Tabs, type TabDef } from "@/components/ui/Tabs";
import { Duration, RelativeTime } from "@/components/ui/Time";
import { IconExternal } from "@/components/shell/Icons";
import { temporalWorkflowUrl } from "@/lib/config";
import { isActiveStatus } from "@/lib/colors";
import { fmtMetric, fmtMs, fmtNum, humanize, shortId } from "@/lib/format";
import { useIncident, useWorkflow } from "@/lib/hooks";
import { displayId, flowRef, type AnomalySignal, type ToolExecution } from "@/lib/types";

type RightTab = "agent" | "metrics" | "audit";

function SignalStrip({ signals }: { signals: AnomalySignal[] }) {
  if (signals.length === 0) return null;
  return (
    <div className="flex items-stretch gap-0 overflow-x-auto border-b border-hairline bg-panel">
      <div className="micro flex shrink-0 items-center border-r border-hairline px-3">Detected signals</div>
      {signals.map((s) => {
        const ratio = s.baseline_value !== 0 ? s.observed_value / s.baseline_value : null;
        return (
          <div key={s.id} className="shrink-0 border-r border-hairline px-3 py-1.5 last:border-r-0" title={s.description}>
            <div className="mono text-[10.5px] text-muted">
              {s.service}
              <span className="text-faint">.{s.metric}</span>
            </div>
            <div className="mono mt-0.5 flex items-baseline gap-1.5 text-[12px]">
              <span className="text-faint">{fmtMetric(s.metric, s.baseline_value)}</span>
              <span className="text-faint">→</span>
              <span className="text-sev2">{fmtMetric(s.metric, s.observed_value)}</span>
              {ratio != null && Number.isFinite(ratio) ? <span className="text-[10.5px] text-muted">{fmtNum(ratio, 1)}×</span> : null}
            </div>
            <div className="mono mt-0.5 text-[9.5px] text-faint">
              {s.deviation_sigma.toFixed(1)}σ · {s.kind} · {s.detector} · {s.window_seconds}s
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ToolExecutionRow({ x }: { x: ToolExecution }) {
  const [open, setOpen] = useState(false);
  const denied = x.status === "denied" || !x.authorization.allowed;
  return (
    <li className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="grid w-full grid-cols-[1fr_auto] items-start gap-x-3 px-3 py-1.5 text-left hover:bg-panel-2" aria-expanded={open}>
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="mono text-[12px] text-ink">{x.tool_name}</span>
            <span className="mono truncate text-[10.5px] text-muted">
              {Object.entries(x.arguments)
                .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                .join(" ")}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-muted">{x.result_summary || x.error || x.authorization.reason}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {x.phase ? <span className="mono text-[10px] text-faint">{x.phase}</span> : null}
          <span className="mono text-[10.5px] text-muted">{fmtMs(x.duration_ms)}</span>
          <ExecStatusChip status={x.status} size="xs" />
        </span>
      </button>
      {open ? (
        <div className="space-y-2 px-3 pb-2.5">
          <GateSequence checks={x.authorization.checks} denialCode={x.authorization.denial_code} reason={x.authorization.reason} />
          <div className="mono flex flex-wrap gap-x-3 text-[10px] text-faint">
            <span>category {x.category}</span>
            <span>effect {x.authorization.effect}</span>
            <span>attempt {x.attempt}</span>
            <span>key {shortId(x.idempotency_key, 12)}</span>
            {x.evidence_ids.length ? <span className="text-ok">+{x.evidence_ids.length} evidence</span> : null}
          </div>
          {denied ? null : <JsonView value={x.result} collapsedDepth={1} />}
        </div>
      ) : null}
    </li>
  );
}

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";
  const q = useIncident(id);
  const [tab, setTab] = useState<RightTab>("agent");

  const detail = q.data;
  const incident = detail?.incident;
  const active = isActiveStatus(incident?.status);
  const workflow = useWorkflow(id, !!incident?.workflow_id);

  const evidence = detail?.evidence?.evidence ?? [];
  const relations = detail?.evidence?.relations ?? [];
  const denials = useMemo(() => (detail?.tool_executions ?? []).filter((x) => !x.authorization.allowed).length, [detail?.tool_executions]);

  const tabs: TabDef<RightTab>[] = [
    { id: "agent", label: "Agent", count: detail?.agent_runs.length ?? null, live: detail?.agent_runs.some((r) => r.status === "running") },
    { id: "metrics", label: "Metrics", count: incident?.affected_services.length ?? null },
    { id: "audit", label: "Audit", count: null },
  ];

  if (q.error) {
    return (
      <div className="p-5">
        <ProblemBanner error={q.error} onRetry={() => void q.refetch()} />
        <div className="mt-3">
          <Link href="/incidents" className="micro hover:text-ink">
            ← incident register
          </Link>
        </div>
      </div>
    );
  }
  if (!detail || !incident) {
    return (
      <div>
        <div className="border-b border-hairline bg-panel px-5 py-4">
          <Skeleton rows={2} />
        </div>
        <Skeleton rows={10} />
      </div>
    );
  }

  const ref = flowRef(incident.flow_name, incident.flow_version);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ---- header ---------------------------------------------------------------------- */}
      <header className="shrink-0 border-b border-hairline bg-panel">
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 pt-3 pb-2.5">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <Link href="/incidents" className="micro hover:text-ink" title="Incident register">
                ←
              </Link>
              <span className="mono text-[20px] leading-none font-medium tracking-[0.02em] text-amber">{displayId(incident)}</span>
              <SevBadge severity={incident.severity} />
              <StatusChip status={incident.status} />
              <span className="flex items-baseline gap-1.5">
                <Duration start={incident.detected_at} end={incident.resolved_at ?? incident.closed_at} live={active} className="text-[16px]" />
                <span className="micro">{active ? "elapsed" : "total"}</span>
              </span>
            </div>
            <h1 className="mt-1.5 text-[15px] leading-snug font-medium text-ink">{incident.title}</h1>
            {incident.summary ? <p className="mt-0.5 max-w-3xl text-[12px] leading-snug text-muted">{incident.summary}</p> : null}
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
              <span className="flex flex-wrap items-center gap-1">
                <span className="micro mr-0.5">affected</span>
                {incident.affected_services.map((s) => (
                  <Tag key={s} accent={s === detail.hypotheses.find((h) => h.id === incident.leading_hypothesis_id)?.suspected_root_cause_service}>
                    {s}
                  </Tag>
                ))}
              </span>
              {ref ? (
                <Link href="/flows" className="mono text-[11px] text-muted hover:text-amber" title="Flow pack">
                  {ref}
                </Link>
              ) : null}
              {incident.workflow_id ? (
                <a
                  href={temporalWorkflowUrl(incident.workflow_id)}
                  target="_blank"
                  rel="noreferrer"
                  className="mono flex items-center gap-1 text-[11px] text-muted hover:text-amber"
                  title="Open in the Temporal Web UI"
                >
                  {shortId(incident.workflow_id, 28)}
                  <IconExternal />
                </a>
              ) : null}
              {workflow.data?.status ? (
                <Chip tone={workflow.data.status === "RUNNING" ? "live" : workflow.data.status === "COMPLETED" ? "ok" : "neutral"} dot pulse={workflow.data.status === "RUNNING"} size="xs" mono>
                  {`workflow ${workflow.data.status.toLowerCase()}`}
                </Chip>
              ) : null}
              <span className="mono text-[10.5px] text-faint">
                detected <RelativeTime iso={incident.detected_at} className="text-[10.5px]" />
                {incident.acknowledged_at ? " · acknowledged" : ""}
                {incident.remediation_attempts > 0 ? ` · ${incident.remediation_attempts} remediation attempt${incident.remediation_attempts > 1 ? "s" : ""}` : ""}
                {` · env ${incident.environment}`}
              </span>
            </div>
          </div>
          <HumanActions incident={incident} />
        </div>
        {incident.root_cause_summary || incident.resolution_summary ? (
          <div className="border-t border-hairline px-5 py-2">
            {incident.root_cause_summary ? (
              <div className="flex gap-2">
                <span className="micro shrink-0 pt-0.5">Root cause</span>
                <span className="text-[12px] leading-snug text-ink">{incident.root_cause_summary}</span>
              </div>
            ) : null}
            {incident.resolution_summary ? (
              <div className="mt-1 flex gap-2">
                <span className="micro shrink-0 pt-0.5">Resolution</span>
                <span className="text-[12px] leading-snug text-ok">{incident.resolution_summary}</span>
              </div>
            ) : null}
          </div>
        ) : null}
      </header>

      <SignalStrip signals={incident.signals} />

      {/* ---- three columns --------------------------------------------------------------- */}
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[380px_minmax(0,1fr)_400px]">
        <div className="min-h-0 border-b border-hairline xl:border-r xl:border-b-0">
          <LiveLedgerPanel incidentId={id} live />
        </div>

        <div className="min-h-0 overflow-y-auto">
          <RootCause hypotheses={detail.hypotheses} leadingId={incident.leading_hypothesis_id} evidence={evidence} />
          <EvidenceGraph evidence={evidence} relations={relations} hypotheses={detail.hypotheses} plans={detail.action_plans} leadingId={incident.leading_hypothesis_id} />
          <Remediation plans={detail.action_plans} approvals={detail.approvals} />
          <VerificationPanel plans={detail.action_plans} />
          <Section
            title="Tool executions"
            meta={`${detail.tool_executions.length} requests${denials > 0 ? ` · ${denials} denied` : ""}`}
            flush
            actions={<span className="micro">every request carries its 14-check trace</span>}
          >
            {detail.tool_executions.length === 0 ? (
              <EmptyState compact title="No tools requested yet" />
            ) : (
              <ul>
                {detail.tool_executions
                  .slice()
                  .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                  .map((x) => (
                    <ToolExecutionRow key={x.id} x={x} />
                  ))}
              </ul>
            )}
          </Section>
          <Section title="Evidence" meta={`${evidence.length} items`} flush>
            {evidence.length === 0 ? (
              <EmptyState compact title="No evidence yet" />
            ) : (
              <ul className="px-3 py-1">
                {evidence
                  .slice()
                  .sort((a, b) => new Date(a.observed_at).getTime() - new Date(b.observed_at).getTime())
                  .map((e) => (
                    <EvidenceListRow key={e.id} id={e.id} kind={e.kind} title={e.title} summary={e.summary} service={e.service} strength={e.strength} observedAt={e.observed_at} />
                  ))}
              </ul>
            )}
          </Section>
        </div>

        <div className="flex min-h-0 flex-col border-t border-hairline xl:border-t-0 xl:border-l">
          <Tabs tabs={tabs} value={tab} onChange={setTab} className="shrink-0 bg-panel" />
          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "agent" ? <AgentActivity runs={detail.agent_runs} /> : null}
            {tab === "metrics" ? (
              <MetricsPanel services={incident.affected_services} signals={incident.signals} detectedAt={incident.detected_at} resolvedAt={incident.resolved_at} />
            ) : null}
            {tab === "audit" ? <AuditPanel incidentId={id} enabled={tab === "audit"} /> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Flat evidence row (the graph shows the relations; this shows the log). */
function EvidenceListRow({
  kind,
  title,
  summary,
  service,
  strength,
  observedAt,
}: {
  id: string;
  kind: string;
  title: string;
  summary: string;
  service: string | null;
  strength: number;
  observedAt: string;
}) {
  return (
    <li className="grid grid-cols-[64px_1fr_auto] items-baseline gap-x-2 border-b border-hairline py-1 last:border-b-0">
      <span className="mono text-[10px] tracking-[0.04em] text-muted uppercase">{humanize(kind)}</span>
      <span className="min-w-0">
        <span className="block truncate text-[12px] text-ink">{title}</span>
        <span className="block truncate text-[11px] text-muted">{summary}</span>
      </span>
      <span className="mono flex shrink-0 items-baseline gap-2 text-[10.5px] text-faint">
        {service ? <span className="text-muted">{service}</span> : null}
        <span>{strength.toFixed(2)}</span>
        <RelativeTime iso={observedAt} className="text-[10.5px]" />
      </span>
    </li>
  );
}
