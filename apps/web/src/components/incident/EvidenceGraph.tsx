"use client";

import { useMemo, useState } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { Section } from "@/components/ui/Section";
import { EmptyState } from "@/components/ui/EmptyState";
import { AMBER, DANGER, evidenceKindColor, FAINT, INFO, MUTED, OK } from "@/lib/colors";
import { fmtScore, humanize } from "@/lib/format";
import type { ActionPlan, Evidence, EvidenceRelation, GraphNodeKind, Hypothesis, RelationKind } from "@/lib/types";
import { useMeasure } from "@/lib/use-measure";

interface GNode extends SimulationNodeDatum {
  id: string;
  kind: GraphNodeKind;
  label: string;
  summary: string;
  color: string;
  r: number;
  leading?: boolean;
}
interface GLink extends SimulationLinkDatum<GNode> {
  kind: RelationKind;
  weight: number;
}

const REL_COLOR: Record<RelationKind, string> = {
  supports: OK,
  contradicts: DANGER,
  caused_by: AMBER,
  depends_on: MUTED,
  correlated_with: MUTED,
  observed_on: FAINT,
  resolved_by: INFO,
};

const HEIGHT = 340;

function buildGraph(evidence: Evidence[], relations: EvidenceRelation[], hypotheses: Hypothesis[], plans: ActionPlan[], leadingId: string | null, width: number) {
  const nodes = new Map<string, GNode>();
  for (const e of evidence) nodes.set(e.id, { id: e.id, kind: "evidence", label: e.title, summary: e.summary, color: evidenceKindColor(e.kind), r: 5 + e.strength * 4 });
  for (const h of hypotheses) nodes.set(h.id, { id: h.id, kind: "hypothesis", label: `H · ${fmtScore(h.confidence)}`, summary: h.statement, color: AMBER, r: 11, leading: h.id === leadingId });
  for (const p of plans) nodes.set(p.id, { id: p.id, kind: "action", label: p.tool_name, summary: `${p.tool_name}(${JSON.stringify(p.arguments)}) · ${p.status}`, color: INFO, r: 9 });
  const ensureService = (name: string) => {
    if (!nodes.has(name)) nodes.set(name, { id: name, kind: "service", label: name, summary: `service ${name}`, color: MUTED, r: 8 });
  };
  for (const r of relations) {
    if (r.from_kind === "service") ensureService(r.from_id);
    if (r.to_kind === "service") ensureService(r.to_id);
  }
  for (const h of hypotheses) if (h.suspected_root_cause_service) ensureService(h.suspected_root_cause_service);

  const links: GLink[] = relations
    .filter((r) => nodes.has(r.from_id) && nodes.has(r.to_id))
    .map((r) => ({ source: r.from_id, target: r.to_id, kind: r.kind, weight: r.weight }));

  const nodeList = Array.from(nodes.values());
  const sim = forceSimulation<GNode>(nodeList)
    .force("link", forceLink<GNode, GLink>(links).id((d) => d.id).distance((l) => (l.kind === "observed_on" ? 70 : l.kind === "supports" || l.kind === "contradicts" ? 60 : 90)).strength(0.6))
    .force("charge", forceManyBody<GNode>().strength(-160))
    .force("collide", forceCollide<GNode>().radius((d) => d.r + 14))
    .force("center", forceCenter(width / 2, HEIGHT / 2))
    .force("x", forceX<GNode>(width / 2).strength(0.05))
    .force("y", forceY<GNode>(HEIGHT / 2).strength(0.08))
    .stop();
  for (let i = 0; i < 300; i++) sim.tick();
  for (const n of nodeList) {
    n.x = Math.max(n.r + 4, Math.min(width - n.r - 4, n.x ?? width / 2));
    n.y = Math.max(n.r + 4, Math.min(HEIGHT - n.r - 4, n.y ?? HEIGHT / 2));
  }
  return { nodes: nodeList, links };
}

function NodeShape({ n, dim, hovered }: { n: GNode; dim: boolean; hovered: boolean }) {
  const x = n.x ?? 0;
  const y = n.y ?? 0;
  const common = { opacity: dim ? 0.25 : 1, style: { transition: "opacity 120ms" } };
  switch (n.kind) {
    case "hypothesis":
      return (
        <g {...common}>
          {n.leading ? <rect x={x - n.r - 4} y={y - n.r - 4} width={(n.r + 4) * 2} height={(n.r + 4) * 2} rx={3} fill="none" stroke={AMBER} strokeOpacity={0.5} className="animate-pulse-amber" /> : null}
          <rect x={x - n.r} y={y - n.r} width={n.r * 2} height={n.r * 2} rx={2} fill={n.leading ? AMBER : "#12151a"} stroke={AMBER} strokeWidth={1.5} />
          <text x={x} y={y + 3.5} textAnchor="middle" style={{ fontSize: 9, fontFamily: "var(--font-mono)", fill: n.leading ? "#0b0d10" : AMBER, fontWeight: 600 }}>
            H
          </text>
        </g>
      );
    case "service":
      return (
        <g {...common}>
          <rect x={x - n.r} y={y - n.r} width={n.r * 2} height={n.r * 2} rx={1} fill="#12151a" stroke={hovered ? "#e8e6e1" : MUTED} strokeWidth={1.25} />
          <text x={x} y={y + n.r + 11} textAnchor="middle" style={{ fontSize: 9, fontFamily: "var(--font-mono)", fill: "#c9cdd3" }}>
            {n.label}
          </text>
        </g>
      );
    case "action":
      return (
        <g {...common}>
          <rect x={x - n.r} y={y - n.r} width={n.r * 2} height={n.r * 2} rx={1} transform={`rotate(45 ${x} ${y})`} fill="#12151a" stroke={INFO} strokeWidth={1.5} />
          <text x={x} y={y + n.r + 13} textAnchor="middle" style={{ fontSize: 9, fontFamily: "var(--font-mono)", fill: INFO }}>
            {n.label}
          </text>
        </g>
      );
    default:
      return (
        <g {...common}>
          <circle cx={x} cy={y} r={n.r} fill={n.color} fillOpacity={hovered ? 1 : 0.85} stroke={hovered ? "#e8e6e1" : "#0b0d10"} strokeWidth={1} />
        </g>
      );
  }
}

export function EvidenceGraph({ evidence, relations, hypotheses, plans, leadingId }: { evidence: Evidence[]; relations: EvidenceRelation[]; hypotheses: Hypothesis[]; plans: ActionPlan[]; leadingId: string | null }) {
  const [ref, { width }] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const w = Math.max(320, width);
  const graph = useMemo(() => buildGraph(evidence, relations, hypotheses, plans, leadingId, w), [evidence, relations, hypotheses, plans, leadingId, w]);
  const hovered = hover ? graph.nodes.find((n) => n.id === hover) ?? null : null;
  const neighbours = useMemo(() => {
    const s = new Set<string>();
    if (!hover) return s;
    for (const l of graph.links) {
      const a = (l.source as GNode).id;
      const b = (l.target as GNode).id;
      if (a === hover) s.add(b);
      if (b === hover) s.add(a);
    }
    return s;
  }, [hover, graph.links]);

  const kinds = Array.from(new Set(evidence.map((e) => e.kind)));

  return (
    <Section title="Evidence graph" meta={`${graph.nodes.length} nodes · ${graph.links.length} edges`} flush>
      {evidence.length === 0 && hypotheses.length === 0 ? (
        <EmptyState compact title="No evidence collected yet" hint="Read-only tools attach evidence; relations connect it to hypotheses, services and actions." />
      ) : (
        <div ref={ref} className="relative graph-paper">
          <svg width="100%" height={HEIGHT} viewBox={`0 0 ${w} ${HEIGHT}`} role="img" aria-label="Evidence graph">
            {graph.links.map((l, i) => {
              const a = l.source as GNode;
              const b = l.target as GNode;
              const related = hover != null && (a.id === hover || b.id === hover);
              const dim = hover != null && !related;
              return (
                <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={REL_COLOR[l.kind]} strokeWidth={related ? 1.6 : 0.9 + l.weight * 0.6} strokeOpacity={dim ? 0.12 : l.kind === "observed_on" ? 0.5 : 0.85} strokeDasharray={l.kind === "contradicts" ? "4 3" : l.kind === "caused_by" ? "6 3" : undefined} />
              );
            })}
            {graph.nodes.map((n) => (
              <g key={n.id} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} style={{ cursor: "default" }}>
                <NodeShape n={n} dim={hover != null && hover !== n.id && !neighbours.has(n.id)} hovered={hover === n.id} />
                <circle cx={n.x} cy={n.y} r={Math.max(12, n.r + 6)} fill="transparent" />
              </g>
            ))}
          </svg>
          {hovered ? (
            <div className="pointer-events-none absolute top-2 left-2 max-w-[60%] rounded-sm border border-hairline-2 bg-panel/95 px-2.5 py-2 text-[11.5px] shadow-none">
              <div className="mono mb-0.5 text-[10px] uppercase tracking-[0.08em]" style={{ color: hovered.color }}>
                {hovered.kind}
              </div>
              <div className="font-medium text-ink">{hovered.label}</div>
              <div className="mt-0.5 leading-snug text-muted">{hovered.summary}</div>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-hairline px-3 py-1.5">
            {kinds.map((k) => (
              <span key={k} className="mono flex items-center gap-1 text-[10px] text-muted">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: evidenceKindColor(k) }} /> {k}
              </span>
            ))}
            <span className="mono flex items-center gap-1 text-[10px] text-muted">
              <span className="inline-block h-2 w-2 rounded-[1px] bg-amber" /> hypothesis
            </span>
            <span className="mono flex items-center gap-1 text-[10px] text-muted">
              <span className="inline-block h-2 w-2 border border-muted" /> service
            </span>
            <span className="mono flex items-center gap-1 text-[10px] text-muted">
              <span className="inline-block h-2 w-2 rotate-45 border border-sev4" /> action
            </span>
            <span className="ml-auto flex flex-wrap gap-x-2">
              {(Object.keys(REL_COLOR) as RelationKind[]).filter((k) => graph.links.some((l) => l.kind === k)).map((k) => (
                <span key={k} className="mono flex items-center gap-1 text-[10px] text-muted">
                  <span className="inline-block h-px w-3" style={{ background: REL_COLOR[k] }} /> {humanize(k)}
                </span>
              ))}
            </span>
          </div>
        </div>
      )}
    </Section>
  );
}
