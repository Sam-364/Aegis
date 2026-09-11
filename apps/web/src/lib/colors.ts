import type { IncidentStatus, Severity } from "@/lib/types";

export const SEV_COLOR: Record<Severity, string> = {
  sev1: "#ff4d4f",
  sev2: "#ff8a3d",
  sev3: "#e6c229",
  sev4: "#4c9be8",
};

export const AMBER = "#f5b31a";
export const OK = "#3ddc97";
export const DANGER = "#ff4d4f";
export const WARN = "#ff8a3d";
export const INFO = "#4c9be8";
export const YELLOW = "#e6c229";
export const MUTED = "#8a9099";
export const FAINT = "#5b6270";
export const INK = "#e8e6e1";
export const HAIRLINE = "#23282f";
export const HAIRLINE_2 = "#2e343d";
export const PANEL = "#12151a";
export const PANEL_2 = "#171b21";
export const CANVAS = "#0b0d10";

export function sevColor(sev: string | null | undefined): string {
  return SEV_COLOR[(sev ?? "") as Severity] ?? MUTED;
}

export type Tone = "neutral" | "live" | "ok" | "warn" | "danger" | "info";

export const TONE_COLOR: Record<Tone, string> = {
  neutral: MUTED,
  live: AMBER,
  ok: OK,
  warn: WARN,
  danger: DANGER,
  info: INFO,
};

/** Semantic tone for each incident status. Live/active phases pulse amber. */
export const STATUS_TONE: Record<IncidentStatus, Tone> = {
  detected: "warn",
  triaging: "live",
  investigating: "live",
  hypothesis_formed: "live",
  validating: "live",
  remediation_planned: "live",
  awaiting_approval: "warn",
  remediating: "live",
  verifying: "live",
  resolved: "ok",
  rolled_back: "danger",
  escalated: "danger",
  failed: "danger",
  closed: "neutral",
};

export const ACTIVE_STATUSES: ReadonlySet<string> = new Set<IncidentStatus>([
  "detected",
  "triaging",
  "investigating",
  "hypothesis_formed",
  "validating",
  "remediation_planned",
  "awaiting_approval",
  "remediating",
  "verifying",
]);

export function isActiveStatus(status: string | null | undefined): boolean {
  return ACTIVE_STATUSES.has(status ?? "");
}

export function statusTone(status: string | null | undefined): Tone {
  return STATUS_TONE[(status ?? "") as IncidentStatus] ?? "neutral";
}

export function riskTone(risk: string | null | undefined): Tone {
  switch ((risk ?? "").toLowerCase()) {
    case "none":
      return "neutral";
    case "low":
      return "ok";
    case "medium":
      return "warn";
    case "high":
    case "critical":
      return "danger";
    default:
      return "neutral";
  }
}

export function categoryTone(category: string | null | undefined): Tone {
  switch (category) {
    case "read_only":
      return "ok";
    case "diagnostic":
      return "info";
    case "mutating":
      return "warn";
    case "dangerous":
      return "danger";
    default:
      return "neutral";
  }
}

export function effectTone(effect: string | null | undefined): Tone {
  switch (effect) {
    case "allow":
      return "ok";
    case "deny":
      return "danger";
    case "require_approval":
      return "live";
    default:
      return "neutral";
  }
}

export function planStatusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "verified":
    case "executed":
    case "approved":
      return "ok";
    case "awaiting_approval":
      return "warn";
    case "executing":
    case "proposed":
      return "live";
    case "policy_denied":
    case "rejected":
    case "execution_failed":
    case "verification_failed":
    case "rolled_back":
      return "danger";
    default:
      return "neutral";
  }
}

export function approvalTone(status: string | null | undefined): Tone {
  switch (status) {
    case "pending":
      return "live";
    case "approved":
      return "ok";
    case "rejected":
      return "danger";
    case "expired":
    case "cancelled":
      return "neutral";
    default:
      return "neutral";
  }
}

export function runStatusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "running":
      return "live";
    case "completed":
      return "ok";
    case "failed":
    case "budget_exhausted":
      return "danger";
    case "cancelled":
      return "neutral";
    default:
      return "neutral";
  }
}

export function execStatusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "succeeded":
      return "ok";
    case "running":
    case "pending":
      return "live";
    case "failed":
    case "timed_out":
    case "denied":
      return "danger";
    case "skipped_duplicate":
      return "warn";
    default:
      return "neutral";
  }
}

export function hypothesisTone(status: string | null | undefined): Tone {
  switch (status) {
    case "confirmed":
      return "ok";
    case "supported":
      return "info";
    case "testing":
    case "proposed":
      return "live";
    case "refuted":
      return "danger";
    case "abandoned":
      return "neutral";
    default:
      return "neutral";
  }
}

export function healthColor(health: string | null | undefined): string {
  switch ((health ?? "").toLowerCase()) {
    case "healthy":
      return OK;
    case "degraded":
      return WARN;
    case "unhealthy":
      return DANGER;
    default:
      return MUTED;
  }
}

/** Infra components report saturation 0..1 instead of a health state. */
export function saturationColor(saturation: number | null | undefined): string {
  if (saturation == null) return MUTED;
  if (saturation >= 0.85) return DANGER;
  if (saturation >= 0.6) return WARN;
  return OK;
}

/** Evidence kinds carry a stable colour drawn from the semantic palette (no purple, no gradients). */
export const EVIDENCE_KIND_COLOR: Record<string, string> = {
  signal: DANGER,
  metric: INFO,
  log: YELLOW,
  trace: OK,
  topology: "#9aa4b2",
  deployment: WARN,
  health: OK,
  diagnostic: "#7fc8ff",
  memory: AMBER,
  observation: MUTED,
  action_result: "#ffb46b",
  verification: "#8be0b8",
};

export function evidenceKindColor(kind: string | null | undefined): string {
  return EVIDENCE_KIND_COLOR[kind ?? ""] ?? MUTED;
}

export function hexWithAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
