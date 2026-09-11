"use client";

import { useMemo } from "react";
import { AMBER, DANGER, FAINT, INFO, MUTED, OK } from "@/lib/colors";
import type { FlowPack, FlowPhase, PhaseTransition } from "@/lib/types";

const TRIGGER_COLOR: Record<PhaseTransition["on"], string> = {
  exit_conditions_met: OK,
  exhausted: DANGER,
  escalate: DANGER,
  no_action_required: INFO,
};

const TRIGGER_LABEL: Record<PhaseTransition["on"], string> = {
  exit_conditions_met: "exit met",
  exhausted: "exhausted",
  escalate: "escalate",
  no_action_required: "no action",
};

const NODE_W = 118;
const NODE_H = 42;
const GAP = 46;
const TOP = 62;
const BOTTOM = 58;

/**
 * Order phases by following `exit_conditions_met` from the initial phase, then append
 * anything unreachable that way (terminal branches such as escalate).
 */
function orderPhases(flow: FlowPack): FlowPhase[] {
  const byName = new Map(flow.phases.map((p) => [p.name, p]));
  const out: FlowPhase[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined = flow.initial_phase;
  while (cursor && byName.has(cursor) && !seen.has(cursor)) {
    const phase = byName.get(cursor);
    if (!phase) break;
    out.push(phase);
    seen.add(cursor);
    cursor = phase.transitions.find((t) => t.on === "exit_conditions_met")?.to;
  }
  for (const p of flow.phases) if (!seen.has(p.name)) out.push(p);
  return out;
}

/** The flow pack as a pipeline: phases left to right, transitions as labelled arcs. */
export function PhaseDiagram({ flow }: { flow: FlowPack }) {
  const ordered = useMemo(() => orderPhases(flow), [flow]);
  const idx = new Map(ordered.map((p, i) => [p.name, i]));
  const width = ordered.length * NODE_W + (ordered.length - 1) * GAP + 24;
  const height = TOP + NODE_H + BOTTOM;
  const cx = (i: number) => 12 + i * (NODE_W + GAP) + NODE_W / 2;

  const arcs = ordered.flatMap((p, i) =>
    p.transitions
      .map((t) => ({ from: i, to: idx.get(t.to), on: t.on }))
      .filter((a): a is { from: number; to: number; on: PhaseTransition["on"] } => a.to != null),
  );

  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} role="img" aria-label={`${flow.name} phase pipeline`} className="block">
        {arcs.map((a, i) => {
          const forwardAdjacent = a.to === a.from + 1 && a.on === "exit_conditions_met";
          const x1 = cx(a.from);
          const x2 = cx(a.to);
          const color = TRIGGER_COLOR[a.on];
          if (forwardAdjacent) {
            const sx = x1 + NODE_W / 2;
            const ex = x2 - NODE_W / 2;
            return (
              <g key={i}>
                <line x1={sx} y1={TOP + NODE_H / 2} x2={ex - 5} y2={TOP + NODE_H / 2} stroke={color} strokeWidth={1.2} markerEnd="url(#flowarrow-ok)" />
                <text x={(sx + ex) / 2} y={TOP + NODE_H / 2 - 5} textAnchor="middle" style={{ fontSize: 8, fontFamily: "var(--font-mono)", fill: MUTED, letterSpacing: "0.06em" }}>
                  {TRIGGER_LABEL[a.on]}
                </text>
              </g>
            );
          }
          // Non-adjacent or non-linear transitions arc below the rail.
          const below = a.to <= a.from || a.on !== "exit_conditions_met";
          const y0 = below ? TOP + NODE_H : TOP;
          const dir = below ? 1 : -1;
          const span = Math.abs(a.to - a.from);
          const lift = Math.min(BOTTOM - 14, 16 + span * 9) * dir;
          const midX = (x1 + x2) / 2;
          const d = `M${x1},${y0} Q${midX},${y0 + lift * 1.6} ${x2},${y0}`;
          return (
            <g key={i} opacity={0.85}>
              <path d={d} fill="none" stroke={color} strokeWidth={1} strokeDasharray="3 3" />
              <circle cx={x2} cy={y0} r={2} fill={color} />
              <text x={midX} y={y0 + lift * 1.05} textAnchor="middle" style={{ fontSize: 8, fontFamily: "var(--font-mono)", fill: color, letterSpacing: "0.06em" }}>
                {TRIGGER_LABEL[a.on]}
              </text>
            </g>
          );
        })}
        <defs>
          <marker id="flowarrow-ok" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z" fill={OK} />
          </marker>
        </defs>
        {ordered.map((p, i) => {
          const initial = p.name === flow.initial_phase;
          const stroke = p.terminal ? FAINT : initial ? AMBER : MUTED;
          return (
            <g key={p.name} transform={`translate(${cx(i) - NODE_W / 2},${TOP})`}>
              <rect width={NODE_W} height={NODE_H} rx={2} fill="#12151a" stroke={stroke} strokeWidth={initial ? 1.5 : 1} />
              <rect width={3} height={NODE_H} fill={stroke} />
              <text x={10} y={17} style={{ fontSize: 11, fontFamily: "var(--font-mono)", fill: "#e8e6e1", letterSpacing: "0.04em" }}>
                {p.name}
              </text>
              <text x={10} y={30} style={{ fontSize: 8.5, fontFamily: "var(--font-mono)", fill: MUTED }}>
                {p.max_iterations} iter · {p.timeout_seconds}s
              </text>
              <text x={NODE_W - 8} y={30} textAnchor="end" style={{ fontSize: 8, fontFamily: "var(--font-mono)", fill: p.risk_ceiling === "none" ? FAINT : AMBER, letterSpacing: "0.08em" }}>
                {p.risk_ceiling.toUpperCase()}
              </text>
              {p.plans_remediation ? <circle cx={NODE_W - 6} cy={6} r={2.5} fill={AMBER} /> : null}
              {initial ? (
                <text x={0} y={-6} style={{ fontSize: 8, fontFamily: "var(--font-mono)", fill: AMBER, letterSpacing: "0.1em" }}>
                  START
                </text>
              ) : null}
              {p.terminal ? (
                <text x={NODE_W} y={-6} textAnchor="end" style={{ fontSize: 8, fontFamily: "var(--font-mono)", fill: FAINT, letterSpacing: "0.1em" }}>
                  TERMINAL
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
