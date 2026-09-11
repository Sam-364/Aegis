"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Ledger } from "@/components/ui/Ledger";
import { LiveDot } from "@/components/ui/LiveDot";
import { ProblemBanner } from "@/components/ui/Problem";
import { Skeleton } from "@/components/ui/Loading";
import { streamUrl } from "@/lib/api";
import { qk, useTimeline } from "@/lib/hooks";
import { useEventSource, type SseStatus } from "@/lib/sse";
import type { IncidentEvent } from "@/lib/types";

function mergeEvents(prev: IncidentEvent[], incoming: IncidentEvent[]): IncidentEvent[] {
  if (incoming.length === 0) return prev;
  const seen = new Set(prev.map((e) => e.seq));
  const add = incoming.filter((e) => !seen.has(e.seq));
  if (add.length === 0) return prev;
  return [...prev, ...add].sort((a, b) => a.seq - b.seq);
}

export function useLiveLedger(incidentId: string, live: boolean): { events: IncidentEvent[]; status: SseStatus; loading: boolean; error: unknown; initialSeq: number; reconnect: () => void; lastSeq: number | null } {
  const qc = useQueryClient();
  const timeline = useTimeline(incidentId);
  /** Only the frames this component received over SSE; the REST replay stays in the query cache. */
  const [streamed, setStreamed] = useState<IncidentEvent[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const events = useMemo(() => mergeEvents(timeline.data ?? [], streamed), [timeline.data, streamed]);
  const initialSeq = useMemo(() => (timeline.data && timeline.data.length > 0 ? Math.max(...timeline.data.map((e) => e.seq)) : 0), [timeline.data]);
  const maxSeq = events.length > 0 ? events[events.length - 1].seq : 0;

  const invalidate = useCallback(() => {
    timer.current = null;
    void qc.invalidateQueries({ queryKey: qk.incident(incidentId) });
    void qc.invalidateQueries({ queryKey: qk.incidentsAll, exact: false, refetchType: "active" });
    void qc.invalidateQueries({ queryKey: qk.approvalsAll, exact: false, refetchType: "active" });
  }, [qc, incidentId]);

  const { status, reconnect, lastSeq } = useEventSource<IncidentEvent>({
    enabled: live && timeline.isSuccess,
    initialSeq: maxSeq,
    buildUrl: (after) => streamUrl(`/api/v1/incidents/${incidentId}/stream`, { after_seq: after ?? 0 }),
    onFrame: (frame) => {
      const ev = frame.data;
      if (!ev || typeof ev.seq !== "number") return;
      setStreamed((prev) => mergeEvents(prev, [ev]));
      if (!timer.current) timer.current = setTimeout(invalidate, 350);
    },
  });

  return { events, status, loading: timeline.isPending, error: timeline.error, initialSeq, reconnect, lastSeq };
}

export function StreamState({ status, onReconnect, lastSeq }: { status: SseStatus; onReconnect: () => void; lastSeq: number | null }) {
  const tone = status === "open" ? "ok" : status === "reconnecting" ? "danger" : status === "connecting" ? "live" : "neutral";
  const text = status === "open" ? "connected" : status === "reconnecting" ? "reconnecting" : status === "connecting" ? "connecting" : status === "idle" ? "idle (terminal)" : "closed";
  return (
    <button type="button" onClick={onReconnect} className="mono flex items-center gap-1.5 text-[10.5px] text-muted hover:text-ink" title="Click to reconnect the incident stream">
      <LiveDot tone={tone} pulse={status === "open" || status === "reconnecting"} size={5} />
      {text}
      {lastSeq != null ? <span className="text-faint">seq {lastSeq}</span> : null}
    </button>
  );
}

export function LiveLedgerPanel({ incidentId, live }: { incidentId: string; live: boolean }) {
  const { events, status, loading, error, initialSeq, reconnect, lastSeq } = useLiveLedger(incidentId, live);
  const [follow, setFollow] = useState(true);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-hairline px-3">
        <div className="flex items-center gap-2">
          <h2 className="micro !text-ink">Ledger</h2>
          <span className="micro-mono">{events.length} events</span>
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setFollow((f) => !f)} className={`micro ${follow ? "text-amber" : ""} hover:text-ink`}>
            {follow ? "following" : "follow"}
          </button>
          <StreamState status={status} onReconnect={reconnect} lastSeq={lastSeq} />
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? <ProblemBanner error={error} compact className="m-3" /> : null}
        {loading && events.length === 0 ? <Skeleton rows={8} /> : <Ledger events={events} freshAfterSeq={initialSeq} follow={follow} />}
      </div>
    </div>
  );
}
