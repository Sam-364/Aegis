"use client";

import { useState } from "react";
import { HypothesisStatusChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { JsonView } from "@/components/ui/JsonView";
import { ConfidenceMeter, Meter, ScoreBreakdown } from "@/components/ui/Meter";
import { Section } from "@/components/ui/Section";
import { evidenceKindColor } from "@/lib/colors";
import { fmtDateTime, fmtScore, fmtTime, humanize } from "@/lib/format";
import type { Evidence, Hypothesis } from "@/lib/types";

export function EvidenceRow({ ev, defaultOpen = false }: { ev: Evidence; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const c = evidenceKindColor(ev.kind);
  return (
    <li className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="grid w-full grid-cols-[10px_1fr_auto] items-start gap-x-2 px-1 py-1.5 text-left hover:bg-panel-2">
        <span className="mt-[5px] inline-block h-[7px] w-[7px] rounded-full" style={{ background: c }} title={ev.kind} />
        <span className="min-w-0">
          <span className="flex items-baseline gap-2">
            <span className="mono text-[10px] uppercase tracking-[0.06em]" style={{ color: c }}>
              {ev.kind}
            </span>
            <span className="truncate text-[12px] text-ink">{ev.title}</span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-muted">{ev.summary}</span>
        </span>
        <span className="flex flex-col items-end gap-0.5">
          <span className="mono text-[10.5px] text-muted">{fmtTime(ev.observed_at)}</span>
          <Meter value={ev.strength} height={3} showValue={false} tone="info" className="w-10" />
        </span>
      </button>
      {open ? (
        <div className="space-y-2 px-1 pb-2 pl-5">
          <p className="text-[11.5px] leading-snug text-ink/90">{ev.summary}</p>
          <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted">
            <span className="mono">source {ev.source}</span>
            {ev.service ? <Tag>{ev.service}</Tag> : null}
            {ev.phase ? <span className="mono">phase {ev.phase}</span> : null}
            <span className="mono">strength {fmtScore(ev.strength)}</span>
            <span className="mono">{fmtDateTime(ev.observed_at)}</span>
            {ev.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
          <JsonView value={ev.data} collapsedDepth={1} />
        </div>
      ) : null}
    </li>
  );
}

function HypothesisCard({ h, evidenceById, leading }: { h: Hypothesis; evidenceById: Map<string, Evidence>; leading: boolean }) {
  const [open, setOpen] = useState(leading);
  const supporting = h.supporting_evidence_ids.map((id) => evidenceById.get(id)).filter((e): e is Evidence => !!e);
  const contradicting = h.contradicting_evidence_ids.map((id) => evidenceById.get(id)).filter((e): e is Evidence => !!e);
  return (
    <div className={`${leading ? "" : "border-t border-hairline"}`}>
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-3 px-3 py-3 text-left hover:bg-panel-2" aria-expanded={open}>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className={`mono text-[10px] uppercase tracking-[0.1em] ${leading ? "text-amber" : "text-muted"}`}>{leading ? "leading hypothesis" : "alternative"}</span>
            <HypothesisStatusChip status={h.status} size="xs" />
            <Tag>{humanize(h.category)}</Tag>
            {h.suspected_root_cause_service ? <Tag accent>{h.suspected_root_cause_service}</Tag> : null}
            <span className="micro-mono ml-auto">{h.proposed_by} · v{h.version}</span>
          </div>
          <p className={`${leading ? "text-[14px] leading-snug font-medium text-ink" : "text-[12.5px] leading-snug text-ink/90"}`}>{h.statement}</p>
        </div>
        <span className="mono w-12 shrink-0 text-right text-[16px] font-medium" style={{ color: h.confidence >= 0.8 ? "#3ddc97" : h.confidence >= 0.55 ? "#f5b31a" : "#ff8a3d" }}>
          {fmtScore(h.confidence)}
        </span>
      </button>
      {open ? (
        <div className="grid gap-4 px-3 pb-4 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-4">
            {h.mechanism ? (
              <div>
                <div className="micro mb-1">Mechanism</div>
                <p className="text-[12px] leading-relaxed text-ink/90">{h.mechanism}</p>
              </div>
            ) : null}
            <div>
              <div className="micro mb-1 flex items-center gap-2">
                Supporting evidence <span className="mono text-ok">{supporting.length}</span>
              </div>
              {supporting.length ? <ul className="border-t border-hairline">{supporting.map((e) => <EvidenceRow key={e.id} ev={e} />)}</ul> : <div className="text-[11.5px] text-faint">none</div>}
            </div>
            <div>
              <div className="micro mb-1 flex items-center gap-2">
                Contradicting evidence <span className="mono text-sev1">{contradicting.length}</span>
              </div>
              {contradicting.length ? <ul className="border-t border-hairline">{contradicting.map((e) => <EvidenceRow key={e.id} ev={e} />)}</ul> : <div className="text-[11.5px] text-faint">none</div>}
            </div>
            <div>
              <div className="micro mb-1 flex items-center gap-2">
                Tests <span className="mono text-muted">{h.tests.length} run · {h.suggested_tests.length} suggested</span>
              </div>
              {h.tests.length ? (
                <ul className="space-y-1.5">
                  {h.tests.map((t) => (
                    <li key={t.id} className="grid grid-cols-[auto_1fr] gap-x-2 text-[11.5px]">
                      <span className={`mono text-[10px] uppercase tracking-[0.06em] ${t.outcome === "confirmed" ? "text-ok" : t.outcome === "refuted" ? "text-sev1" : "text-muted"}`}>{t.outcome}</span>
                      <span>
                        <span className="mono text-ink">{t.tool_name}</span> <span className="text-muted">— {t.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {h.suggested_tests.length ? (
                <ul className="mt-1.5 space-y-1">
                  {h.suggested_tests.map((t, i) => (
                    <li key={i} className="text-[11px] text-muted">
                      <span className="mono text-ink/80">{t.tool_name}</span> {t.would_confirm ? "would confirm" : "would refute"}: {t.expectation}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
          <div className="space-y-3">
            <ConfidenceMeter value={h.confidence} />
            <div>
              <div className="micro mb-1.5">Deterministic score</div>
              <ScoreBreakdown score={h.score} />
            </div>
            {h.score.explanation.length ? (
              <ul className="space-y-0.5 border-t border-hairline pt-2">
                {h.score.explanation.map((line, i) => (
                  <li key={i} className="text-[11px] leading-snug text-muted">
                    {line}
                  </li>
                ))}
              </ul>
            ) : null}
            {h.affected_services.length ? (
              <div className="flex flex-wrap gap-1">
                {h.affected_services.map((s) => (
                  <Tag key={s}>{s}</Tag>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function RootCause({ hypotheses, leadingId, evidence }: { hypotheses: Hypothesis[]; leadingId: string | null; evidence: Evidence[] }) {
  const evidenceById = new Map(evidence.map((e) => [e.id, e]));
  const leading = hypotheses.find((h) => h.id === leadingId) ?? hypotheses[0] ?? null;
  const others = hypotheses.filter((h) => h.id !== leading?.id);
  return (
    <Section title="Root cause" meta={`${hypotheses.length} hypotheses · ${evidence.length} evidence`} flush>
      {!leading ? (
        <EmptyState compact title="No hypothesis yet" hint="The agent proposes hypotheses once triage has gathered enough evidence; the runtime scores them deterministically." />
      ) : (
        <>
          <HypothesisCard h={leading} evidenceById={evidenceById} leading />
          {others.map((h) => (
            <HypothesisCard key={h.id} h={h} evidenceById={evidenceById} leading={false} />
          ))}
        </>
      )}
    </Section>
  );
}
