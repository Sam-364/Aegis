"use client";

import { useState } from "react";
import { IncidentTable } from "@/components/incident/IncidentTable";
import { Button } from "@/components/ui/Button";
import { Pagination } from "@/components/ui/Pagination";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { sevColor, statusTone, TONE_COLOR } from "@/lib/colors";
import { humanize } from "@/lib/format";
import { useIncidents } from "@/lib/hooks";
import { INCIDENT_STATUSES, SEVERITIES, type IncidentStatus, type Severity } from "@/lib/types";

const LIMIT = 50;

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export default function IncidentsPage() {
  const [status, setStatus] = useState<IncidentStatus[]>([]);
  const [severity, setSeverity] = useState<Severity[]>([]);
  const [active, setActive] = useState(false);
  const [offset, setOffset] = useState(0);
  const q = useIncidents({ status, severity, active, limit: LIMIT, offset });
  const total = q.data?.total ?? 0;

  return (
    <div>
      <PageHeader kicker="Incidents" title="Incident register" meta={<span className="mono">{q.data ? `${total} matching` : "…"}</span>} actions={
        (status.length || severity.length || active) ? (
          <Button size="sm" variant="ghost" onClick={() => { setStatus([]); setSeverity([]); setActive(false); setOffset(0); }}>
            clear filters
          </Button>
        ) : null
      }>
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="micro mr-1 w-14">Severity</span>
            {SEVERITIES.map((s) => {
              const on = severity.includes(s);
              const c = sevColor(s);
              return (
                <button key={s} type="button" onClick={() => { setSeverity(toggle(severity, s)); setOffset(0); }} className="mono h-[20px] rounded-xs border px-2 text-[10.5px] uppercase transition-colors" style={{ color: on ? "#0b0d10" : c, borderColor: c, background: on ? c : "transparent" }}>
                  {s}
                </button>
              );
            })}
            <span className="mx-2 h-4 w-px bg-hairline" />
            <button type="button" onClick={() => { setActive((a) => !a); setOffset(0); }} className={`h-[20px] rounded-xs border px-2 font-sans text-[10.5px] tracking-[0.06em] uppercase transition-colors ${active ? "border-amber bg-amber text-canvas" : "border-hairline-2 text-muted hover:text-ink"}`}>
              active only
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="micro mr-1 w-14">Status</span>
            {INCIDENT_STATUSES.map((s) => {
              const on = status.includes(s);
              const c = TONE_COLOR[statusTone(s)];
              return (
                <button key={s} type="button" onClick={() => { setStatus(toggle(status, s)); setOffset(0); }} className="h-[20px] rounded-xs border px-2 font-sans text-[10.5px] tracking-[0.02em] transition-colors" style={{ color: on ? "#0b0d10" : "#8a9099", borderColor: on ? c : "#2e343d", background: on ? c : "transparent" }}>
                  {humanize(s)}
                </button>
              );
            })}
          </div>
        </div>
      </PageHeader>
      {q.error ? <ProblemBanner error={q.error} className="m-4" onRetry={() => void q.refetch()} /> : q.isPending ? <Skeleton rows={6} /> : <IncidentTable incidents={q.data.items} emptyTitle="No incidents match" emptyHint="Loosen the filters or wait for the detector." />}
      {q.data ? <Pagination offset={offset} limit={LIMIT} total={total} onChange={setOffset} /> : null}
    </div>
  );
}
