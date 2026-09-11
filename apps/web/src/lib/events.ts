import type { IncidentEvent } from "@/lib/types";

/** Every IncidentEvent.type the runtime can emit. Used to subscribe to named SSE frames. */
export const EVENT_TYPES = [
  "incident.detected",
  "incident.created",
  "incident.signal_attached",
  "incident.status_changed",
  "incident.resolved",
  "incident.escalated",
  "incident.closed",
  "incident.failed",
  "flow.selected",
  "flow.phase_entered",
  "flow.phase_exited",
  "agent.run_started",
  "agent.step",
  "agent.run_finished",
  "tool.requested",
  "tool.authorized",
  "tool.denied",
  "tool.executed",
  "tool.failed",
  "evidence.collected",
  "hypothesis.created",
  "hypothesis.updated",
  "hypothesis.validated",
  "hypothesis.refuted",
  "remediation.proposed",
  "policy.decided",
  "approval.requested",
  "approval.decided",
  "approval.expired",
  "remediation.started",
  "remediation.completed",
  "remediation.failed",
  "remediation.skipped_duplicate",
  "verification.started",
  "verification.passed",
  "verification.failed",
  "rollback.started",
  "rollback.completed",
  "rollback.failed",
  "memory.stored",
  "budget.exhausted",
  "llm.fallback",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export type EventTone = "neutral" | "live" | "ok" | "warn" | "danger" | "info" | "amber";

/** Tone for a ledger row; colour stays strictly semantic (failures red, success green, human/gate amber). */
export function eventTone(type: string): EventTone {
  if (
    type.endsWith(".failed") ||
    type.endsWith(".denied") ||
    type.endsWith(".refuted") ||
    type === "incident.escalated" ||
    type === "budget.exhausted" ||
    type === "rollback.started"
  )
    return "danger";
  if (
    type.endsWith(".passed") ||
    type.endsWith(".validated") ||
    type === "remediation.completed" ||
    type === "incident.resolved" ||
    type === "rollback.completed" ||
    type === "memory.stored"
  )
    return "ok";
  if (type.startsWith("approval.") || type === "policy.decided" || type === "tool.authorized") return "amber";
  if (type === "llm.fallback" || type === "approval.expired" || type === "remediation.skipped_duplicate") return "warn";
  if (type.startsWith("remediation.") || type.startsWith("verification.")) return "live";
  if (type.startsWith("hypothesis.") || type.startsWith("evidence.")) return "info";
  return "neutral";
}

/** Events that mark a decision or a boundary and deserve emphasis in the ledger. */
export const MILESTONE_TYPES: ReadonlySet<string> = new Set([
  "incident.detected",
  "incident.created",
  "flow.selected",
  "hypothesis.created",
  "hypothesis.validated",
  "hypothesis.refuted",
  "remediation.proposed",
  "policy.decided",
  "approval.requested",
  "approval.decided",
  "approval.expired",
  "remediation.started",
  "remediation.completed",
  "remediation.failed",
  "verification.passed",
  "verification.failed",
  "rollback.completed",
  "rollback.failed",
  "incident.resolved",
  "incident.escalated",
  "incident.closed",
  "incident.failed",
  "budget.exhausted",
  "tool.denied",
]);

export function isPhaseBoundary(type: string): boolean {
  return type === "flow.phase_entered" || type === "flow.phase_exited";
}

/** Single-letter glyph for the actor kind. */
export function actorGlyph(kind: string | null | undefined): string {
  switch ((kind ?? "").toLowerCase()) {
    case "human":
      return "H";
    case "agent":
      return "A";
    case "system":
      return "S";
    case "workflow":
      return "W";
    case "detector":
      return "D";
    case "api":
      return "I";
    default:
      return (kind ?? "?").charAt(0).toUpperCase() || "?";
  }
}

export function actorLabel(kind: string | null | undefined): string {
  switch ((kind ?? "").toLowerCase()) {
    case "human":
      return "Human";
    case "agent":
      return "Agent";
    case "system":
      return "System";
    case "workflow":
      return "Workflow";
    case "detector":
      return "Detector";
    case "api":
      return "API";
    default:
      return kind ?? "Unknown";
  }
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Phase named by an event, if any (phase_entered/exited, run_started/finished, steps). */
export function eventPhase(ev: IncidentEvent): string | null {
  return str(ev.payload.phase);
}

export interface PhaseBand {
  /** Phase name; null for events before the flow started. */
  phase: string | null;
  enteredAt: string | null;
  exitedAt: string | null;
  /** Transition trigger recorded on `flow.phase_exited`, when known. */
  trigger: string | null;
  events: IncidentEvent[];
}

/**
 * Group an ordered timeline into phase bands.
 *
 * Bands are opened by `flow.phase_entered` or `agent.run_started` (whichever names a new phase
 * first) and closed by `flow.phase_exited`, whose `next_phase` pre-announces the following band.
 * Events that arrive between an exit and the next entry attach to the announced next phase.
 */
export function groupByPhase(events: IncidentEvent[]): PhaseBand[] {
  const bands: PhaseBand[] = [];
  let current: PhaseBand = { phase: null, enteredAt: null, exitedAt: null, trigger: null, events: [] };
  let pending: string | null = null;

  const push = () => {
    if (current.events.length > 0) bands.push(current);
  };
  const openBand = (phase: string | null, at: string | null) => {
    push();
    current = { phase, enteredAt: at, exitedAt: null, trigger: null, events: [] };
  };

  for (const ev of events) {
    const named = eventPhase(ev);
    const opens = ev.type === "flow.phase_entered" || ev.type === "agent.run_started";

    if (opens && named && named !== current.phase) {
      openBand(named, ev.at);
      pending = null;
    } else if (pending && named !== current.phase && current.exitedAt) {
      // First event after an exit: belongs to the announced next phase.
      openBand(pending, ev.at);
      pending = null;
    } else if (current.exitedAt && named && named !== current.phase) {
      openBand(named, ev.at);
      pending = null;
    }

    current.events.push(ev);

    if (ev.type === "flow.phase_exited") {
      current.exitedAt = ev.at;
      current.trigger = str(ev.payload.trigger);
      pending = str(ev.payload.next_phase);
    }
  }
  push();
  return bands;
}

/** One-line human summary of the payload for ticker / ledger rows. */
export function eventSummary(ev: IncidentEvent): string | null {
  const p = ev.payload;
  switch (ev.type) {
    case "incident.status_changed":
      return `${str(p.from) ?? "?"} → ${str(p.to) ?? "?"}`;
    case "flow.phase_exited":
      return `${str(p.trigger) ?? ""} → ${str(p.next_phase) ?? "end"}`.trim();
    case "agent.step": {
      const action = str(p.action);
      const model = str(p.model);
      return [action, model].filter(Boolean).join(" · ") || null;
    }
    case "tool.executed":
      return str(p.summary);
    case "tool.denied":
      return `${str(p.denial_code) ?? "denied"} · ${str(p.reason) ?? ""}`;
    case "policy.decided":
      return `${str(p.effect) ?? ""} · ${str(p.matched_rule) ?? str(p.invariant) ?? ""}`;
    case "approval.decided":
      return typeof p.approved === "boolean" ? (p.approved ? "approved" : "rejected") : null;
    case "verification.passed":
    case "verification.failed":
      return str(p.summary);
    case "hypothesis.created":
    case "hypothesis.updated":
    case "hypothesis.validated":
    case "hypothesis.refuted": {
      const c = typeof p.confidence === "number" ? `confidence ${p.confidence.toFixed(2)}` : null;
      return [str(p.status), c].filter(Boolean).join(" · ") || null;
    }
    case "remediation.proposed":
      return `${str(p.tool) ?? ""} · risk ${str(p.risk) ?? "?"}`;
    case "memory.stored":
      return str(p.outcome);
    case "llm.fallback":
      return str(p.error);
    default:
      return null;
  }
}
