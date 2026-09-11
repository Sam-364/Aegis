"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { streamUrl } from "@/lib/api";
import { qk } from "@/lib/hooks";
import { useEventSource, type SseStatus } from "@/lib/sse";
import type { IncidentEvent } from "@/lib/types";

const CAP = 200;

interface GlobalStreamValue {
  status: SseStatus;
  attempts: number;
  /** Newest first, capped at 200. */
  events: IncidentEvent[];
  reconnect: () => void;
  receivedCount: number;
}

const Ctx = createContext<GlobalStreamValue>({ status: "idle", attempts: 0, events: [], reconnect: () => undefined, receivedCount: 0 });

/**
 * Global firehose (`/api/v1/events/stream`). Feeds the ticker, the top-strip indicator and
 * invalidates list/stat queries so every page stays fresh without polling hard.
 */
export function GlobalStreamProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [events, setEvents] = useState<IncidentEvent[]>([]);
  const [receivedCount, setReceivedCount] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<Set<string>>(new Set());

  const flush = useCallback(() => {
    timer.current = null;
    const ids = Array.from(pending.current);
    pending.current.clear();
    void qc.invalidateQueries({ queryKey: qk.incidentsAll, exact: false, refetchType: "active" });
    void qc.invalidateQueries({ queryKey: qk.approvalsAll, exact: false, refetchType: "active" });
    void qc.invalidateQueries({ queryKey: qk.agentRuns, exact: false, refetchType: "active" });
    void qc.invalidateQueries({ queryKey: qk.notifications, exact: false, refetchType: "active" });
    for (const id of ids) void qc.invalidateQueries({ queryKey: qk.incident(id), refetchType: "active" });
  }, [qc]);

  const { status, attempts, reconnect } = useEventSource<IncidentEvent>({
    buildUrl: () => streamUrl("/api/v1/events/stream"),
    onFrame: (frame) => {
      const ev = frame.data;
      if (!ev || typeof ev !== "object" || typeof ev.seq !== "number") return;
      setEvents((prev) => {
        if (prev.some((p) => p.id === ev.id)) return prev;
        const next = [ev, ...prev];
        return next.length > CAP ? next.slice(0, CAP) : next;
      });
      setReceivedCount((n) => n + 1);
      pending.current.add(ev.incident_id);
      if (!timer.current) timer.current = setTimeout(flush, 400);
    },
  });

  const value = useMemo<GlobalStreamValue>(() => ({ status, attempts, events, reconnect, receivedCount }), [status, attempts, events, reconnect, receivedCount]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useGlobalStream(): GlobalStreamValue {
  return useContext(Ctx);
}
