"use client";

import { config } from "@/lib/config";
import { useCallback, useEffect, useRef, useState } from "react";
import { EVENT_TYPES } from "@/lib/events";

export type SseStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export interface SseFrame<T> {
  /** SSE `id:` field (incident stream: the seq; global stream: `<incident_id>:<seq>`) or null. */
  id: string | null;
  /** SSE `event:` field (the IncidentEvent.type). */
  type: string;
  data: T;
}

export interface UseEventSourceOptions<T> {
  /** Called for every parsed frame. The latest callback is always used; no need to memoise. */
  onFrame: (frame: SseFrame<T>) => void;
  /** Build the URL for a (re)connection given the last seen numeric seq. Return null to stay idle. */
  buildUrl: (afterSeq: number | null) => string | null;
  /** Initial `after_seq` (e.g. the last seq already loaded through REST). */
  initialSeq?: number | null;
  enabled?: boolean;
  eventTypes?: readonly string[];
}

const MAX_BACKOFF_MS = 15_000;

/**
 * Native EventSource wrapper with explicit reconnection.
 *
 * The browser's built-in retry re-uses the original URL, so on error we close the source and
 * re-open it ourselves with `after_seq=<last seq seen>` (exponential backoff, capped at 15s).
 * The server also honours Last-Event-ID for the browser's own retries.
 */
export function useEventSource<T = unknown>(opts: UseEventSourceOptions<T>): {
  status: SseStatus;
  lastSeq: number | null;
  attempts: number;
  reconnect: () => void;
} {
  const {
    buildUrl,
    eventTypes = EVENT_TYPES,
    initialSeq = null,
    // A recording has no live stream to subscribe to; the fixtures already carry the whole
    // timeline, so the console shows it as idle rather than failing to connect forever.
    enabled = !config.demo,
  } = opts;
  const [connStatus, setConnStatus] = useState<SseStatus>(enabled ? "connecting" : "idle");
  const [lastSeq, setLastSeq] = useState<number | null>(initialSeq);
  const [attempts, setAttempts] = useState(0);
  const onFrameRef = useRef(opts.onFrame);
  const buildUrlRef = useRef(buildUrl);
  const lastSeqRef = useRef<number | null>(initialSeq);
  const esRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const [nonce, setNonce] = useState(0);

  // Keep the latest callbacks reachable from the async EventSource handlers without
  // re-opening the connection, and without writing refs during render.
  useEffect(() => {
    onFrameRef.current = opts.onFrame;
    buildUrlRef.current = buildUrl;
  }, [opts.onFrame, buildUrl]);

  // Adopt a later initialSeq (REST timeline loaded after the stream opened).
  useEffect(() => {
    if (initialSeq != null && (lastSeqRef.current == null || initialSeq > lastSeqRef.current)) {
      lastSeqRef.current = initialSeq;
      setLastSeq(initialSeq);
    }
  }, [initialSeq]);

  const reconnect = useCallback(() => {
    attemptRef.current = 0;
    setAttempts(0);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;

    const open = () => {
      if (disposed) return;
      const url = buildUrlRef.current(lastSeqRef.current);
      if (!url) {
        setConnStatus("idle");
        return;
      }
      setConnStatus(attemptRef.current === 0 ? "connecting" : "reconnecting");
      const es = new EventSource(url);
      esRef.current = es;

      const handle = (e: MessageEvent<string>) => {
        let data: T;
        try {
          data = JSON.parse(e.data) as T;
        } catch {
          return;
        }
        const id = e.lastEventId || null;
        const seq = readSeq(id, data);
        if (seq != null) {
          lastSeqRef.current = seq;
          setLastSeq(seq);
        }
        onFrameRef.current({ id, type: e.type === "message" ? readType(data) : e.type, data });
      };

      es.addEventListener("message", handle);
      for (const t of eventTypes) es.addEventListener(t, handle as EventListener);

      es.onopen = () => {
        attemptRef.current = 0;
        setAttempts(0);
        setConnStatus("open");
      };
      es.onerror = () => {
        es.close();
        if (disposed) return;
        setConnStatus("reconnecting");
        attemptRef.current += 1;
        setAttempts(attemptRef.current);
        const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(attemptRef.current - 1, 4));
        timerRef.current = setTimeout(open, delay);
      };
    };

    open();

    return () => {
      disposed = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      esRef.current?.close();
      esRef.current = null;
      setConnStatus("closed");
    };
    // eventTypes is a module constant; nonce forces a manual reconnect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, nonce]);

  return { status: enabled ? connStatus : "idle", lastSeq, attempts, reconnect };
}

/** Incident streams send `id: <seq>`; the global stream sends `<incident_id>:<seq>`. */
function readSeq(id: string | null, data: unknown): number | null {
  if (id) {
    const n = Number(id.includes(":") ? id.slice(id.lastIndexOf(":") + 1) : id);
    if (Number.isFinite(n)) return n;
  }
  if (data && typeof data === "object" && "seq" in data) {
    const n = Number((data as { seq?: unknown }).seq);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function readType(data: unknown): string {
  if (data && typeof data === "object" && typeof (data as { type?: unknown }).type === "string") {
    return (data as { type: string }).type;
  }
  return "message";
}
