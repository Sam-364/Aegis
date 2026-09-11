"use client";

import { useMemo, useState } from "react";
import { AMBER, DANGER, FAINT, healthColor, hexWithAlpha, MUTED, OK, saturationColor } from "@/lib/colors";
import { fmtMs, fmtPct } from "@/lib/format";
import { isServiceState, type Fault, type Topology, type TopologyNode } from "@/lib/types";

export interface ServiceMapProps {
  topology: Topology;
  /** Services that appear in active incidents; drawn with an amber halo. */
  highlight?: ReadonlySet<string>;
  mini?: boolean;
  selected?: string | null;
  onSelect?: (name: string | null) => void;
  className?: string;
}

interface Placed {
  node: TopologyNode;
  x: number;
  y: number;
}

function ringColor(n: TopologyNode): string {
  if (!n.state.up) return DANGER;
  return isServiceState(n.state) ? healthColor(n.state.health) : saturationColor(n.state.saturation);
}

function kindGlyph(kind: TopologyNode["kind"]): string {
  switch (kind) {
    case "gateway":
      return "GW";
    case "database":
      return "DB";
    case "cache":
      return "KV";
    default:
      return "SVC";
  }
}

export function faultTargets(faults: Fault[]): Set<string> {
  const s = new Set<string>();
  for (const f of faults) {
    if (!f.active) continue;
    const p = f.params;
    for (const key of ["service", "target", "component"]) {
      const v = p[key];
      if (typeof v === "string") s.add(v);
    }
  }
  return s;
}

/** Layered SVG service map: tiers left to right, curved edges, live health rings, animated traffic. */
export function ServiceMap({ topology, highlight, mini = false, selected = null, onSelect, className = "" }: ServiceMapProps) {
  const [hover, setHover] = useState<string | null>(null);
  const NODE_W = mini ? 68 : 150;
  const NODE_H = mini ? 18 : 48;
  const COL_GAP = mini ? 34 : 110;
  const ROW_GAP = mini ? 8 : 22;
  const PAD = mini ? 6 : 24;

  const { placed, width, height, byName } = useMemo(() => {
    const tiers = new Map<number, TopologyNode[]>();
    for (const n of topology.nodes) {
      const list = tiers.get(n.tier) ?? [];
      list.push(n);
      tiers.set(n.tier, list);
    }
    const tierKeys = Array.from(tiers.keys()).sort((a, b) => a - b);
    const maxRows = Math.max(1, ...tierKeys.map((t) => tiers.get(t)?.length ?? 0));
    const h = PAD * 2 + maxRows * NODE_H + (maxRows - 1) * ROW_GAP;
    const w = PAD * 2 + tierKeys.length * NODE_W + (tierKeys.length - 1) * COL_GAP;
    const out: Placed[] = [];
    tierKeys.forEach((t, ci) => {
      const list = (tiers.get(t) ?? []).slice().sort((a, b) => a.name.localeCompare(b.name));
      const colH = list.length * NODE_H + (list.length - 1) * ROW_GAP;
      const y0 = (h - colH) / 2;
      list.forEach((n, ri) => {
        out.push({ node: n, x: PAD + ci * (NODE_W + COL_GAP), y: y0 + ri * (NODE_H + ROW_GAP) });
      });
    });
    const map = new Map(out.map((p) => [p.node.name, p]));
    return { placed: out, width: w, height: h, byName: map };
  }, [topology.nodes, NODE_W, NODE_H, COL_GAP, ROW_GAP, PAD]);

  const faulted = useMemo(() => faultTargets(topology.active_faults), [topology.active_faults]);
  const focus = hover ?? selected;

  const edgePath = (a: Placed, b: Placed) => {
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const dx = Math.max(24, (x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" className={`block ${className}`} style={{ maxHeight: mini ? undefined : "100%" }} role="img" aria-label="Service topology">
      <defs>
        <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" fill={FAINT} />
        </marker>
      </defs>
      {/* edges */}
      {topology.edges.map((e) => {
        const a = byName.get(e.source);
        const b = byName.get(e.target);
        if (!a || !b) return null;
        const tgt = ringColor(b.node);
        const related = focus != null && (e.source === focus || e.target === focus);
        const dim = focus != null && !related;
        const color = tgt === OK ? FAINT : tgt;
        return (
          <g key={`${e.source}-${e.target}`} opacity={dim ? 0.18 : 1}>
            <path d={edgePath(a, b)} fill="none" stroke={related ? AMBER : color} strokeWidth={related ? 1.5 : e.critical ? 1.1 : 0.8} strokeDasharray={e.critical ? undefined : "3 3"} markerEnd={mini ? undefined : "url(#arrow)"} />
            {a.node.state.up && b.node.state.up ? (
              <path d={edgePath(a, b)} fill="none" stroke={related ? AMBER : tgt} strokeWidth={mini ? 1 : 1.6} strokeDasharray="2 12" strokeLinecap="round" className="animate-flow" opacity={related ? 0.9 : 0.55} />
            ) : null}
          </g>
        );
      })}
      {/* nodes */}
      {placed.map(({ node, x, y }) => {
        const ring = ringColor(node);
        const hl = highlight?.has(node.name) ?? false;
        const isFault = faulted.has(node.name);
        const dim = focus != null && focus !== node.name && !topology.edges.some((e) => (e.source === focus && e.target === node.name) || (e.target === focus && e.source === node.name));
        const svc = isServiceState(node.state) ? node.state : null;
        const infra = isServiceState(node.state) ? null : node.state;
        return (
          <g
            key={node.name}
            transform={`translate(${x},${y})`}
            opacity={dim ? 0.35 : 1}
            onMouseEnter={() => setHover(node.name)}
            onMouseLeave={() => setHover(null)}
            onClick={() => onSelect?.(selected === node.name ? null : node.name)}
            style={{ cursor: onSelect ? "pointer" : "default" }}
          >
            {hl ? <rect x={-3} y={-3} width={NODE_W + 6} height={NODE_H + 6} rx={3} fill="none" stroke={AMBER} strokeWidth={1} strokeOpacity={0.9} className="animate-pulse-amber" /> : null}
            <rect width={NODE_W} height={NODE_H} rx={2} fill={selected === node.name ? "#1d222a" : "#12151a"} stroke={ring} strokeWidth={mini ? 1 : 1.5} />
            <rect x={0} y={0} width={mini ? 2 : 3} height={NODE_H} fill={ring} />
            {mini ? (
              <text x={6} y={NODE_H / 2 + 3} style={{ fontSize: 7.5, fontFamily: "var(--font-mono)", fill: "#e8e6e1" }}>
                {node.name.replace("-service", "")}
              </text>
            ) : (
              <>
                <text x={10} y={16} style={{ fontSize: 11.5, fontFamily: "var(--font-mono)", fill: "#e8e6e1", fontWeight: 500 }}>
                  {node.name}
                </text>
                <text x={NODE_W - 8} y={16} textAnchor="end" style={{ fontSize: 8.5, fontFamily: "var(--font-mono)", fill: MUTED, letterSpacing: "0.08em" }}>
                  {kindGlyph(node.kind)}
                </text>
                <text x={10} y={31} style={{ fontSize: 9.5, fontFamily: "var(--font-mono)", fill: MUTED }}>
                  {svc ? `p95 ${fmtMs(svc.latency_p95_ms)}` : infra ? `${infra.connections} conn` : ""}
                </text>
                <text x={NODE_W - 8} y={31} textAnchor="end" style={{ fontSize: 9.5, fontFamily: "var(--font-mono)", fill: svc ? (svc.error_rate > 0.05 ? DANGER : svc.error_rate > 0.01 ? "#ff8a3d" : MUTED) : saturationColor(infra?.saturation) }}>
                  {svc ? `err ${fmtPct(svc.error_rate, 2)}` : infra ? `sat ${fmtPct(infra.saturation, 0)}` : ""}
                </text>
                <text x={10} y={42} style={{ fontSize: 8.5, fontFamily: "var(--font-mono)", fill: FAINT }}>
                  v{node.version} · {node.replicas}×
                </text>
                {isFault ? (
                  <g transform={`translate(${NODE_W - 40},36)`}>
                    <rect width={32} height={9} rx={1} fill={hexWithAlpha(DANGER, 0.15)} stroke={DANGER} strokeWidth={0.75} />
                    <text x={16} y={7} textAnchor="middle" style={{ fontSize: 6.5, fontFamily: "var(--font-mono)", fill: DANGER, letterSpacing: "0.1em" }}>
                      FAULT
                    </text>
                  </g>
                ) : null}
              </>
            )}
            {mini && isFault ? <circle cx={NODE_W - 4} cy={4} r={2} fill={DANGER} /> : null}
          </g>
        );
      })}
    </svg>
  );
}
