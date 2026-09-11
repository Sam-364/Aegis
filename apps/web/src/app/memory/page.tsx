"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Chip, SevBadge, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { Meter } from "@/components/ui/Meter";
import { Pagination } from "@/components/ui/Pagination";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { RelativeTime } from "@/components/ui/Time";
import { fmtDuration, fmtPct, humanize } from "@/lib/format";
import { useMemories, useMemorySearch, useTopology } from "@/lib/hooks";
import type { IncidentMemory } from "@/lib/types";

const LIMIT = 25;

function outcomeTone(outcome: IncidentMemory["outcome"]): "ok" | "danger" | "neutral" {
  return outcome === "resolved" ? "ok" : outcome === "escalated" ? "danger" : "neutral";
}

function Bullets({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="micro mb-1">{label}</div>
      <ul className="space-y-0.5">
        {items.map((s, i) => (
          <li key={i} className="flex gap-2 text-[11.5px] leading-snug text-ink/90">
            <span className="mt-[6px] inline-block h-[3px] w-[3px] shrink-0 rounded-full bg-muted" />
            {s}
          </li>
        ))}
      </ul>
    </div>
  );
}

function MemoryRow({ m, similarity, matchedOn }: { m: IncidentMemory; similarity?: number; matchedOn?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="grid w-full grid-cols-[1fr_auto] items-start gap-x-4 px-4 py-2.5 text-left hover:bg-panel-2" aria-expanded={open}>
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <span className="mono text-[11.5px] text-amber">INC-{m.incident_number}</span>
            <SevBadge severity={m.severity} size="xs" />
            <Chip tone={outcomeTone(m.outcome)} dot size="xs">
              {humanize(m.outcome)}
            </Chip>
            <Tag>{humanize(m.root_cause_category)}</Tag>
            {m.root_cause_service ? <Tag accent>{m.root_cause_service}</Tag> : null}
            <span className="mono text-[10.5px] text-faint">{fmtDuration(m.duration_seconds)}</span>
            {matchedOn ? <span className="mono text-[10px] text-muted">matched on {matchedOn}</span> : null}
          </span>
          <span className="mt-1 block text-[12.5px] leading-snug text-ink">{m.title}</span>
          <span className="mt-0.5 block text-[11.5px] leading-snug text-muted">{m.root_cause}</span>
        </span>
        <span className="flex w-[130px] shrink-0 flex-col items-end gap-1">
          {similarity != null ? (
            <>
              <span className="mono text-[14px] text-ok">{fmtPct(similarity, 1)}</span>
              <Meter value={similarity} tone="ok" height={4} showValue={false} className="w-full" />
            </>
          ) : null}
          <RelativeTime iso={m.resolved_at ?? m.created_at} className="text-[10.5px] text-faint" />
        </span>
      </button>
      {open ? (
        <div className="grid gap-5 px-4 pt-1 pb-4 lg:grid-cols-2">
          <div className="space-y-3">
            <Bullets label="Symptoms" items={m.symptoms} />
            <Bullets label="Evidence" items={m.evidence_summary} />
            <div>
              <div className="micro mb-1">Affected services</div>
              <div className="flex flex-wrap gap-1">
                {m.affected_services
                  .slice()
                  .sort()
                  .map((s) => (
                    <Tag key={s}>{s}</Tag>
                  ))}
              </div>
            </div>
          </div>
          <div className="space-y-3">
            <div>
              <div className="micro mb-1">Resolution</div>
              <p className="text-[11.5px] leading-snug text-ink/90">{m.resolution || "—"}</p>
            </div>
            <div>
              <div className="micro mb-1">Verification</div>
              <p className="text-[11.5px] leading-snug text-ink/90">{m.verification_summary || "—"}</p>
            </div>
            {m.actions.length ? (
              <div>
                <div className="micro mb-1">Actions taken</div>
                <ul className="space-y-0.5">
                  {m.actions.map((a, i) => (
                    <li key={i} className="mono text-[11px] text-muted">
                      {Object.entries(a)
                        .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                        .join(" ")}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <Bullets label="Lessons" items={m.lessons} />
            <div className="mono border-t border-hairline pt-2 text-[10px] text-faint">
              <Link href={`/incidents/${m.incident_id}`} className="hover:text-amber">
                open incident →
              </Link>
              {m.embedding_model ? <span className="ml-3">embedded with {m.embedding_model}</span> : null}
            </div>
          </div>
        </div>
      ) : null}
    </li>
  );
}

export default function MemoryPage() {
  const [q, setQ] = useState("");
  const [services, setServices] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const list = useMemories(LIMIT, offset);
  const search = useMemorySearch(q, services);
  const topo = useTopology(false);

  const serviceOptions = useMemo(() => {
    const fromTopo = (topo.data?.nodes ?? []).map((n) => n.name);
    const fromMemories = (list.data?.items ?? []).flatMap((m) => m.affected_services);
    return Array.from(new Set([...fromTopo, ...fromMemories])).sort();
  }, [topo.data, list.data]);

  const searching = q.trim().length >= 2;

  return (
    <div>
      <PageHeader
        kicker="Memory"
        title="What the runtime learned"
        meta={
          <>
            <span className="mono">{list.data ? `${list.data.total} memories` : "…"}</span>
            <span className="text-faint">
              After each incident the runtime extracts a structured memory and embeds it in pgvector. The next incident retrieves similar ones as evidence of kind{" "}
              <span className="mono">memory</span>.
            </span>
          </>
        }
      >
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search memories by symptom, root cause or lesson (min 2 characters)…"
              className="field w-full max-w-[560px]"
            />
            {searching ? (
              <button type="button" onClick={() => { setQ(""); setServices([]); }} className="micro hover:text-ink">
                clear
              </button>
            ) : null}
          </div>
          {serviceOptions.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="micro mr-1">Filter services</span>
              {serviceOptions.map((s) => {
                const on = services.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setServices((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))}
                    className={`mono h-[20px] rounded-xs border px-1.5 text-[10.5px] transition-colors ${on ? "border-amber bg-amber text-canvas" : "border-hairline-2 text-muted hover:text-ink"}`}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      </PageHeader>

      {searching ? (
        <Section title="Search results" meta={search.data ? `${search.data.length} hits · cosine similarity` : "…"} flush>
          {search.error ? (
            <ProblemBanner error={search.error} className="m-3" onRetry={() => void search.refetch()} />
          ) : search.isPending ? (
            <Skeleton rows={4} />
          ) : (search.data ?? []).length === 0 ? (
            <EmptyState compact title="No similar memories" hint="Try fewer service filters or a broader phrase." />
          ) : (
            <ul>
              {(search.data ?? []).map((hit) => (
                <MemoryRow key={hit.memory.id} m={hit.memory} similarity={hit.similarity} matchedOn={hit.matched_on} />
              ))}
            </ul>
          )}
        </Section>
      ) : null}

      <Section title="All memories" meta={list.data ? `${list.data.total}` : undefined} flush>
        {list.error ? (
          <ProblemBanner error={list.error} className="m-3" onRetry={() => void list.refetch()} />
        ) : list.isPending ? (
          <Skeleton rows={6} />
        ) : (list.data?.items ?? []).length === 0 ? (
          <EmptyState title="No memories yet" hint="A memory is written when an incident reaches a terminal state." />
        ) : (
          <>
            <ul>
              {(list.data?.items ?? []).map((m) => (
                <MemoryRow key={m.id} m={m} />
              ))}
            </ul>
            <Pagination offset={offset} limit={LIMIT} total={list.data?.total ?? 0} onChange={setOffset} />
          </>
        )}
      </Section>
    </div>
  );
}
