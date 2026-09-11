"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TONE_COLOR } from "@/lib/colors";
import { actorGlyph, actorLabel, eventSummary, eventTone, groupByPhase, MILESTONE_TYPES, type EventTone } from "@/lib/events";
import { fmtTime } from "@/lib/format";
import type { IncidentEvent } from "@/lib/types";
import { Duration } from "@/components/ui/Time";
import { JsonView } from "@/components/ui/JsonView";
import { EmptyState } from "@/components/ui/EmptyState";

const TONE_HEX: Record<EventTone, string> = {
  neutral: "#5b6270",
  live: TONE_COLOR.live,
  ok: TONE_COLOR.ok,
  warn: TONE_COLOR.warn,
  danger: TONE_COLOR.danger,
  info: TONE_COLOR.info,
  amber: TONE_COLOR.live,
};

function LedgerRow({ ev, fresh }: { ev: IncidentEvent; fresh: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = TONE_HEX[eventTone(ev.type)];
  const milestone = MILESTONE_TYPES.has(ev.type);
  const summary = eventSummary(ev);
  const hasPayload = ev.payload && Object.keys(ev.payload).length > 0;
  return (
    <li className={`group relative ${fresh ? "animate-slide-in" : ""}`}>
      <button
        type="button"
        onClick={() => hasPayload && setOpen((o) => !o)}
        className={`grid w-full grid-cols-[56px_18px_1fr] items-start gap-x-2 px-3 py-[5px] text-left hover:bg-panel-2 ${hasPayload ? "cursor-pointer" : "cursor-default"}`}
        aria-expanded={open}
      >
        <span className="mono pt-[1px] text-[11px] tabular-nums text-muted">{fmtTime(ev.at)}</span>
        <span
          className="mono mt-[1px] inline-flex h-[15px] w-[15px] items-center justify-center rounded-xs border text-[9px] leading-none"
          style={{ color: tone, borderColor: `${tone}80`, background: `${tone}14` }}
          title={`${actorLabel(ev.actor.kind)} · ${ev.actor.id}`}
        >
          {actorGlyph(ev.actor.kind)}
        </span>
        <span className="min-w-0">
          <span className="flex items-baseline gap-2">
            <span className={`min-w-0 text-[12px] leading-snug ${milestone ? "font-medium text-ink" : "text-ink/90"}`}>{ev.title}</span>
            {hasPayload ? <span className="mono ml-auto shrink-0 text-[10px] text-faint group-hover:text-muted">{open ? "−" : "+"}</span> : null}
          </span>
          <span className="mt-[1px] flex items-baseline gap-2">
            <span className="mono text-[10px] tracking-[0.02em]" style={{ color: tone }}>
              {ev.type}
            </span>
            {summary ? <span className="truncate text-[11px] text-muted">{summary}</span> : null}
          </span>
        </span>
      </button>
      {open ? (
        <div className="px-3 pb-2 pl-[88px]">
          <JsonView value={ev.payload} collapsedDepth={2} />
        </div>
      ) : null}
      <span className="absolute top-0 bottom-0 left-0 w-[2px]" style={{ background: milestone ? tone : "transparent" }} aria-hidden />
    </li>
  );
}

export function Ledger({ events, freshAfterSeq = Infinity, follow = true, className = "" }: { events: IncidentEvent[]; freshAfterSeq?: number; follow?: boolean; className?: string }) {
  const bands = useMemo(() => groupByPhase(events), [events]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const lastSeq = events.length > 0 ? events[events.length - 1].seq : 0;

  useEffect(() => {
    if (follow) endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [lastSeq, follow]);

  if (events.length === 0) return <EmptyState compact title="No events yet" hint="The ledger fills as the runtime records incident events." className={className} />;

  return (
    <div className={className}>
      {bands.map((band, i) => {
        const closed = band.exitedAt != null;
        return (
          <section key={`${band.phase ?? "pre"}-${i}`} className="border-b border-hairline last:border-b-0">
            <header className="sticky top-0 z-10 flex h-7 items-center gap-2 border-b border-hairline bg-panel/95 px-3 backdrop-blur-[2px]">
              <span className={`mono text-[10.5px] tracking-[0.1em] uppercase ${band.phase ? (closed ? "text-ink" : "text-amber") : "text-muted"}`}>
                {band.phase ?? "detection"}
              </span>
              {!closed && band.phase ? <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber animate-pulse-amber" aria-hidden /> : null}
              <span className="mono text-[10.5px] text-muted">{band.events.length}</span>
              <span className="ml-auto flex items-center gap-2">
                {band.trigger ? <span className="micro-mono">{band.trigger}</span> : null}
                <Duration start={band.enteredAt ?? band.events[0]?.at} end={band.exitedAt ?? (closed ? band.events[band.events.length - 1]?.at : null)} live={!closed} className="text-[10.5px]" />
              </span>
            </header>
            <ol className="py-1">
              {band.events.map((ev) => (
                <LedgerRow key={ev.id} ev={ev} fresh={ev.seq > freshAfterSeq} />
              ))}
            </ol>
          </section>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
