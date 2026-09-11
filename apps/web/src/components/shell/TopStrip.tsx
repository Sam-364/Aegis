"use client";

import Link from "next/link";
import { useGlobalStream } from "@/components/shell/GlobalStream";
import { LiveDot } from "@/components/ui/LiveDot";
import { UtcClock } from "@/components/ui/Time";
import { useIncidentStats, useMe, useReady, useSystemInfo } from "@/lib/hooks";
import type { SseStatus } from "@/lib/sse";

function sseLabel(status: SseStatus): { text: string; tone: "ok" | "live" | "danger" | "neutral"; pulse: boolean } {
  switch (status) {
    case "open":
      return { text: "stream live", tone: "ok", pulse: true };
    case "connecting":
      return { text: "connecting", tone: "live", pulse: true };
    case "reconnecting":
      return { text: "reconnecting", tone: "danger", pulse: true };
    case "closed":
      return { text: "stream closed", tone: "neutral", pulse: false };
    default:
      return { text: "stream idle", tone: "neutral", pulse: false };
  }
}

export function TopStrip() {
  const info = useSystemInfo();
  const me = useMe();
  const ready = useReady();
  const stats = useIncidentStats();
  const stream = useGlobalStream();
  const sse = sseLabel(stream.status);
  const env = info.data?.environment;
  const envColor = env === "production" ? "#ff4d4f" : env === "staging" ? "#ff8a3d" : "#8a9099";
  const pending = stats.data?.pending_approvals ?? 0;
  const readyOk = ready.data?.status === "ready";

  return (
    <header className="flex h-9 shrink-0 items-center gap-4 border-b border-hairline bg-panel px-4">
      <div className="flex items-center gap-2">
        <span className="font-sans text-[12px] font-semibold tracking-[0.18em] text-ink">AEGIS</span>
        <span className="micro hidden sm:inline">incident-response runtime</span>
      </div>
      <div className="mono flex items-center gap-1.5 text-[10.5px] tracking-[0.06em] uppercase" style={{ color: envColor }} title="API environment">
        <span className="inline-block h-[8px] w-[3px]" style={{ background: envColor }} />
        {env ?? "…"}
      </div>
      <div className="ml-auto flex items-center gap-4">
        <Link href="/system" className="flex items-center gap-1.5 text-[11px] text-muted hover:text-ink" title="Readiness">
          <LiveDot tone={ready.isError ? "danger" : readyOk ? "ok" : ready.data ? "danger" : "neutral"} size={6} />
          <span className="mono">{ready.isError ? "api down" : readyOk ? "ready" : ready.data ? "not ready" : "…"}</span>
        </Link>
        <button type="button" onClick={stream.reconnect} className="flex items-center gap-1.5 text-[11px] text-muted hover:text-ink" title={`SSE ${stream.status}${stream.attempts ? ` · attempt ${stream.attempts}` : ""} · ${stream.receivedCount} events received. Click to reconnect.`}>
          <LiveDot tone={sse.tone} pulse={sse.pulse} size={6} />
          <span className="mono">{sse.text}</span>
        </button>
        <Link href="/approvals" className={`flex items-center gap-1.5 text-[11px] ${pending > 0 ? "text-amber" : "text-muted hover:text-ink"}`} title="Pending approvals">
          <span className={`mono inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-xs px-1 text-[10px] ${pending > 0 ? "bg-amber text-canvas animate-pulse-amber" : "border border-hairline-2"}`}>{pending}</span>
          <span className="hidden md:inline">approvals</span>
        </Link>
        <div className="flex items-center gap-1.5 text-[11px] text-muted" title={me.data ? `${me.data.id} · roles ${me.data.roles.join(", ")} · auth ${me.data.auth_mode}` : undefined}>
          <span className="mono inline-flex h-4 w-4 items-center justify-center rounded-xs border border-hairline-2 text-[9px] text-ink">
            {(me.data?.display_name ?? "?").charAt(0).toUpperCase()}
          </span>
          <span className="hidden lg:inline">{me.data?.display_name ?? "…"}</span>
          {me.data ? <span className="micro-mono hidden xl:inline">{me.data.roles[0]}</span> : null}
        </div>
        <UtcClock className="text-[11px]" />
      </div>
    </header>
  );
}
