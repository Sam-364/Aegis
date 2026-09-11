"use client";

import { useMemo, useSyncExternalStore } from "react";

/**
 * Ticking clock as an external store.
 *
 * The snapshot is quantised to `intervalMs` so it is stable between ticks (React requires a
 * cached snapshot), and the subscription is a plain interval — no setState during an effect.
 */
export function useNow(intervalMs = 1000): number {
  const store = useMemo(() => {
    const snapshot = () => Math.floor(Date.now() / intervalMs) * intervalMs;
    return {
      subscribe: (onChange: () => void) => {
        const t = setInterval(onChange, intervalMs);
        return () => clearInterval(t);
      },
      snapshot,
    };
  }, [intervalMs]);
  return useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
}

const NOOP_SUBSCRIBE = (): (() => void) => () => undefined;
const TRUE = (): boolean => true;
const FALSE = (): boolean => false;

/** True once hydrated on the client; use to gate content that cannot match server HTML. */
export function useMounted(): boolean {
  return useSyncExternalStore(NOOP_SUBSCRIBE, TRUE, FALSE);
}
