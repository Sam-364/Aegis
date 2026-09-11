import type { ReactNode } from "react";
import {
  approvalTone,
  categoryTone,
  effectTone,
  execStatusTone,
  hexWithAlpha,
  hypothesisTone,
  isActiveStatus,
  planStatusTone,
  riskTone,
  runStatusTone,
  sevColor,
  statusTone,
  TONE_COLOR,
  type Tone,
} from "@/lib/colors";
import { humanize } from "@/lib/format";
import { LiveDot } from "@/components/ui/LiveDot";

export interface ChipProps {
  tone?: Tone;
  color?: string;
  dot?: boolean;
  pulse?: boolean;
  mono?: boolean;
  size?: "xs" | "sm";
  filled?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}

/** Rectangular label with a hairline border. Only the dot is round. */
export function Chip({ tone = "neutral", color, dot = false, pulse = false, mono = false, size = "sm", filled = false, className = "", title, children }: ChipProps) {
  const c = color ?? TONE_COLOR[tone];
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-xs border whitespace-nowrap align-middle ${mono ? "mono" : "font-sans"} ${
        size === "xs" ? "h-[16px] px-1 text-[10px]" : "h-[18px] px-1.5 text-[11px]"
      } ${className}`}
      style={{
        color: filled ? "#0b0d10" : c,
        borderColor: filled ? c : hexWithAlpha(c, 0.45),
        background: filled ? c : hexWithAlpha(c, 0.08),
        letterSpacing: mono ? "0.04em" : undefined,
      }}
    >
      {dot ? <LiveDot color={filled ? "#0b0d10" : c} pulse={pulse} size={5} /> : null}
      {children}
    </span>
  );
}

export function SevBadge({ severity, size = "sm", className = "" }: { severity: string; size?: "xs" | "sm"; className?: string }) {
  return (
    <Chip color={sevColor(severity)} mono size={size} className={`uppercase font-semibold ${className}`} title={`Severity ${severity.toUpperCase()}`}>
      {severity.toUpperCase()}
    </Chip>
  );
}

export function StatusChip({ status, size = "sm", className = "" }: { status: string; size?: "xs" | "sm"; className?: string }) {
  const live = isActiveStatus(status) && status !== "awaiting_approval" && status !== "detected";
  return (
    <Chip tone={statusTone(status)} dot pulse={live} size={size} className={className} title={`Incident status: ${status}`}>
      {humanize(status)}
    </Chip>
  );
}

export function RiskChip({ risk, size = "sm", className = "" }: { risk: string; size?: "xs" | "sm"; className?: string }) {
  return (
    <Chip tone={riskTone(risk)} mono size={size} className={`uppercase ${className}`} title={`Risk ${risk}`}>
      {risk}
    </Chip>
  );
}

export function CategoryChip({ category, size = "sm" }: { category: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={categoryTone(category)} mono size={size} className="uppercase">
      {humanize(category)}
    </Chip>
  );
}

export function EffectChip({ effect, size = "sm" }: { effect: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={effectTone(effect)} dot size={size}>
      {humanize(effect)}
    </Chip>
  );
}

export function PlanStatusChip({ status, size = "sm" }: { status: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={planStatusTone(status)} dot pulse={status === "executing" || status === "awaiting_approval"} size={size}>
      {humanize(status)}
    </Chip>
  );
}

export function ApprovalStatusChip({ status, size = "sm" }: { status: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={approvalTone(status)} dot pulse={status === "pending"} size={size}>
      {humanize(status)}
    </Chip>
  );
}

export function RunStatusChip({ status, size = "sm" }: { status: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={runStatusTone(status)} dot pulse={status === "running"} size={size}>
      {humanize(status)}
    </Chip>
  );
}

export function ExecStatusChip({ status, size = "sm" }: { status: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={execStatusTone(status)} dot pulse={status === "running"} size={size}>
      {humanize(status)}
    </Chip>
  );
}

export function HypothesisStatusChip({ status, size = "sm" }: { status: string; size?: "xs" | "sm" }) {
  return (
    <Chip tone={hypothesisTone(status)} dot pulse={status === "testing"} size={size}>
      {humanize(status)}
    </Chip>
  );
}

/** Plain identifier tag (service names, tool names). */
export function Tag({ children, className = "", title, accent = false }: { children: ReactNode; className?: string; title?: string; accent?: boolean }) {
  return (
    <span
      title={title}
      className={`mono inline-flex h-[18px] items-center rounded-xs border px-1.5 text-[11px] whitespace-nowrap ${
        accent ? "border-amber/50 bg-amber/10 text-amber" : "border-hairline-2 bg-panel-2 text-ink"
      } ${className}`}
    >
      {children}
    </span>
  );
}
