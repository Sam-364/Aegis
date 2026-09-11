"use client";

import { useState } from "react";
import { Chip } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { JsonView } from "@/components/ui/JsonView";
import { ProblemBanner } from "@/components/ui/Problem";
import { Skeleton } from "@/components/ui/Loading";
import { actorGlyph, actorLabel, eventTone } from "@/lib/events";
import { fmtTimeMs, shortId } from "@/lib/format";
import { useAudit } from "@/lib/hooks";
import { TONE_COLOR } from "@/lib/colors";
import type { AuditEvent } from "@/lib/types";

function decisionTone(decision: string | null): "ok" | "danger" | "warn" | "neutral" {
  if (!decision) return "neutral";
  const d = decision.toLowerCase();
  if (d.includes("allow") || d.includes("approved") || d.includes("pass")) return "ok";
  if (d.includes("deny") || d.includes("reject") || d.includes("fail")) return "danger";
  if (d.includes("approval")) return "warn";
  return "neutral";
}

function AuditRow({ e }: { e: AuditEvent }) {
  const [open, setOpen] = useState(false);
  const tone = eventTone(e.event_type);
  const color = TONE_COLOR[tone === "amber" ? "live" : tone];
  const hasData = e.data && Object.keys(e.data).length > 0;
  return (
    <li className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => hasData && setOpen((o) => !o)} className={`grid w-full grid-cols-[76px_16px_1fr] items-start gap-x-2 px-3 py-1.5 text-left hover:bg-panel-2 ${hasData ? "" : "cursor-default"}`}>
        <span className="mono pt-[1px] text-[10.5px] text-muted tabular-nums">{fmtTimeMs(e.at)}</span>
        <span
          className="mono mt-[1px] inline-flex h-[14px] w-[14px] items-center justify-center rounded-xs border text-[8.5px] leading-none"
          style={{ color, borderColor: `${color}80` }}
          title={`${actorLabel(e.actor.kind)} · ${e.actor.id}`}
        >
          {actorGlyph(e.actor.kind)}
        </span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="mono text-[11px]" style={{ color }}>
              {e.event_type}
            </span>
            {e.decision ? (
              <Chip tone={decisionTone(e.decision)} size="xs" mono>
                {e.decision}
              </Chip>
            ) : null}
            {e.tool_name ? <span className="mono text-[10.5px] text-ink">{e.tool_name}</span> : null}
            {e.phase ? <span className="mono text-[10px] text-faint">{e.phase}</span> : null}
          </span>
          {e.reason ? <span className="mt-0.5 block text-[11px] leading-snug text-muted">{e.reason}</span> : null}
          {e.action && e.action !== e.event_type ? <span className="mono block text-[10.5px] text-faint">{e.action}</span> : null}
        </span>
      </button>
      {open ? (
        <div className="px-3 pb-2 pl-[98px]">
          <JsonView value={e.data} collapsedDepth={1} />
          <div className="mono mt-1 flex flex-wrap gap-x-3 text-[10px] text-faint">
            <span>id {shortId(e.id, 8)}</span>
            {e.trace_id ? <span>trace {shortId(e.trace_id, 12)}</span> : null}
            {e.agent_run_id ? <span>run {shortId(e.agent_run_id, 8)}</span> : null}
            {e.tool_execution_id ? <span>exec {shortId(e.tool_execution_id, 8)}</span> : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

/** Append-only audit trail. The database trigger refuses updates and deletes; this is the record. */
export function AuditPanel({ incidentId, enabled }: { incidentId: string; enabled: boolean }) {
  const audit = useAudit(incidentId, enabled);
  if (audit.error) return <ProblemBanner error={audit.error} compact className="m-3" onRetry={() => void audit.refetch()} />;
  if (audit.isPending) return <Skeleton rows={8} />;
  const rows = audit.data ?? [];
  if (rows.length === 0) return <EmptyState compact title="No audit events" hint="Every authorization, decision and mutation is appended here and can never be rewritten." />;
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-1.5">
        <span className="micro">Append-only</span>
        <span className="mono text-[10.5px] text-faint">{rows.length} entries · newest first</span>
      </div>
      <ul>
        {rows.map((e) => (
          <AuditRow key={e.id} e={e} />
        ))}
      </ul>
    </div>
  );
}
