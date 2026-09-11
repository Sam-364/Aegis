"use client";

import { useMemo, useState } from "react";
import { ToolDrawer } from "@/components/tools/ToolDrawer";
import { Chip, RiskChip } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { DANGER, INFO, OK, WARN } from "@/lib/colors";
import { humanize } from "@/lib/format";
import { useTools } from "@/lib/hooks";
import type { ToolCategory, ToolSpec } from "@/lib/types";

const CATEGORY_ORDER: ToolCategory[] = ["read_only", "diagnostic", "mutating", "dangerous"];

const CATEGORY_NOTE: Record<ToolCategory, string> = {
  read_only: "Observation only. The agent may request these inside the reasoning loop.",
  diagnostic: "Deeper probes, still non-mutating. Allowed inside the loop.",
  mutating: "Changes the system. Never runs inside the agent loop — only the workflow executes it, after policy evaluation and (usually) human approval.",
  dangerous: "Registered so the gate can refuse them. Never executable, by any actor, under any policy.",
};

const CATEGORY_COLOR: Record<ToolCategory, string> = {
  read_only: OK,
  diagnostic: INFO,
  mutating: WARN,
  dangerous: DANGER,
};

function ToolRow({ t, onOpen }: { t: ToolSpec; onOpen: () => void }) {
  const args = Object.keys(t.arguments_schema.properties ?? {});
  const required = new Set(t.arguments_schema.required ?? []);
  return (
    <tr onClick={onOpen} className="cursor-pointer">
      <td>
        <span className="mono text-[12px] text-ink">{t.name}</span>
        <span className="mono ml-1.5 text-[10px] text-faint">v{t.version}</span>
        {!t.enabled ? (
          <Chip tone="danger" size="xs" mono className="ml-1.5">
            disabled
          </Chip>
        ) : null}
      </td>
      <td className="max-w-[520px]">
        <div className="truncate text-[11.5px] text-muted">{t.description}</div>
      </td>
      <td>
        <RiskChip risk={t.risk} size="xs" />
      </td>
      <td className="mono text-[10.5px] text-muted">
        {args.length === 0 ? (
          <span className="text-faint">—</span>
        ) : (
          args.map((a) => (
            <span key={a} className={required.has(a) ? "text-ink" : ""}>
              {a}
              {required.has(a) ? "" : "?"}{" "}
            </span>
          ))
        )}
      </td>
      <td className="mono text-right text-[10.5px] text-muted">{t.timeout_seconds}s</td>
      <td className="mono text-[10.5px] text-muted">{t.idempotent ? "idempotent" : "—"}</td>
      <td className="mono text-[10.5px] text-muted">{t.produces_evidence ? "evidence" : "—"}</td>
    </tr>
  );
}

export default function ToolsPage() {
  const q = useTools();
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const grouped = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const list = (q.data ?? []).filter((t) => !needle || t.name.toLowerCase().includes(needle) || t.description.toLowerCase().includes(needle));
    const map = new Map<ToolCategory, ToolSpec[]>();
    for (const c of CATEGORY_ORDER) map.set(c, []);
    for (const t of list) {
      const bucket = map.get(t.category);
      if (bucket) bucket.push(t);
      else map.set(t.category, [t]);
    }
    return map;
  }, [q.data, filter]);

  const total = q.data?.length ?? 0;

  return (
    <div>
      <PageHeader
        kicker="Tool registry"
        title="What the runtime can be asked to do"
        meta={
          <>
            <span className="mono">{total} tools</span>
            {CATEGORY_ORDER.map((c) => (
              <span key={c} className="mono flex items-center gap-1.5 text-[11px]">
                <span className="inline-block h-2 w-2 rounded-xs" style={{ background: CATEGORY_COLOR[c] }} />
                {grouped.get(c)?.length ?? 0} {humanize(c)}
              </span>
            ))}
          </>
        }
        actions={<input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="filter tools…" className="field mono w-[200px]" />}
      />
      {q.error ? (
        <ProblemBanner error={q.error} className="m-4" onRetry={() => void q.refetch()} />
      ) : q.isPending ? (
        <Skeleton rows={10} />
      ) : total === 0 ? (
        <EmptyState title="No tools registered" />
      ) : (
        CATEGORY_ORDER.map((c) => {
          const tools = (grouped.get(c) ?? []).slice().sort((a, b) => a.name.localeCompare(b.name));
          return (
            <Section
              key={c}
              title={
                <span className="flex items-center gap-2">
                  <span className="inline-block h-2.5 w-[3px]" style={{ background: CATEGORY_COLOR[c] }} />
                  {humanize(c)}
                </span>
              }
              meta={`${tools.length}`}
              flush
              actions={
                c === "dangerous" ? (
                  <span className="mono rounded-xs border border-sev1/50 bg-sev1/10 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-sev1 uppercase">never executable</span>
                ) : c === "mutating" ? (
                  <span className="mono rounded-xs border border-sev2/50 bg-sev2/10 px-1.5 py-0.5 text-[10px] tracking-[0.1em] text-sev2 uppercase">workflow only</span>
                ) : null
              }
            >
              <div className="border-b border-hairline px-3 py-1.5 text-[11.5px] leading-snug text-muted">{CATEGORY_NOTE[c]}</div>
              {tools.length === 0 ? (
                <div className="px-3 py-3">
                  <span className="mono text-[11px] text-faint">no matches</span>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="table-grid">
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>Description</th>
                        <th>Risk</th>
                        <th>Arguments</th>
                        <th className="text-right">Timeout</th>
                        <th />
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {tools.map((t) => (
                        <ToolRow key={t.name} t={t} onOpen={() => setOpen(t.name)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          );
        })
      )}
      <ToolDrawer name={open} onClose={() => setOpen(null)} />
    </div>
  );
}
