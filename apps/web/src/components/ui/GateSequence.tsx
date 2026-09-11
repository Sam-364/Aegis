"use client";

import { useState } from "react";
import { DANGER, FAINT, OK } from "@/lib/colors";
import { humanize } from "@/lib/format";
import { GATE_CHECKS, type GateCheck } from "@/lib/types";

export type LightState = "pass" | "fail" | "not_reached";

export function gateStates(checks: GateCheck[]): { name: string; state: LightState; detail: string | null }[] {
  const byName = new Map(checks.map((c) => [c.name, c]));
  return GATE_CHECKS.map((name) => {
    const c = byName.get(name);
    if (!c) return { name, state: "not_reached" as LightState, detail: null };
    return { name, state: c.passed ? ("pass" as LightState) : ("fail" as LightState), detail: c.detail };
  });
}

/**
 * The 14 authorization checks as a row of lights: pass / fail / not reached.
 * The failing check shows the denial code; hover any light for its detail.
 */
export function GateSequence({
  checks,
  denialCode,
  reason,
  compact = false,
  className = "",
}: {
  checks: GateCheck[];
  denialCode?: string | null;
  reason?: string | null;
  compact?: boolean;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const states = gateStates(checks);
  const failIdx = states.findIndex((s) => s.state === "fail");
  const active = hover ?? (failIdx >= 0 ? failIdx : null);
  const passed = states.filter((s) => s.state === "pass").length;

  return (
    <div className={className}>
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-[3px]" role="list" aria-label="Authorization checks">
          {states.map((s, i) => {
            const color = s.state === "pass" ? OK : s.state === "fail" ? DANGER : FAINT;
            return (
              <button
                key={s.name}
                type="button"
                role="listitem"
                aria-label={`${humanize(s.name)}: ${s.state}`}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                className="rounded-xs outline-none transition-transform duration-100 hover:scale-y-125 focus-visible:ring-1 focus-visible:ring-amber"
                style={{
                  width: compact ? 8 : 12,
                  height: compact ? 10 : 14,
                  background: s.state === "not_reached" ? "transparent" : color,
                  border: `1px solid ${color}`,
                  opacity: s.state === "not_reached" ? 0.6 : 1,
                  boxShadow: s.state === "fail" ? `0 0 6px ${DANGER}` : undefined,
                }}
              />
            );
          })}
        </div>
        <span className="mono text-[11px] text-muted">
          {passed}/{GATE_CHECKS.length}
        </span>
        {failIdx >= 0 && denialCode ? (
          <span className="mono rounded-xs border border-sev1/50 bg-sev1/10 px-1.5 text-[10.5px] uppercase tracking-[0.06em] text-sev1">{denialCode}</span>
        ) : failIdx < 0 && passed === GATE_CHECKS.length ? (
          <span className="mono text-[10.5px] uppercase tracking-[0.06em] text-ok">authorized</span>
        ) : null}
      </div>
      {!compact || active != null ? (
        <div className="mt-1.5 min-h-[28px] text-[11.5px] leading-snug">
          {active != null ? (
            <div className="flex gap-2">
              <span className="mono shrink-0 text-muted">
                {String(active + 1).padStart(2, "0")} {humanize(states[active].name)}
              </span>
              <span className={states[active].state === "fail" ? "text-sev1" : states[active].state === "pass" ? "text-ink" : "text-faint"}>
                {states[active].detail ?? "not reached"}
              </span>
            </div>
          ) : reason ? (
            <span className="text-muted">{reason}</span>
          ) : (
            <span className="text-faint">Hover a light for the check detail.</span>
          )}
        </div>
      ) : null}
    </div>
  );
}
