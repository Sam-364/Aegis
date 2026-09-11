"use client";

import { CategoryChip, Chip, RiskChip, Tag } from "@/components/ui/Chip";
import { Drawer } from "@/components/ui/Drawer";
import { JsonView } from "@/components/ui/JsonView";
import { KeyValue } from "@/components/ui/KeyValue";
import { ProblemBanner } from "@/components/ui/Problem";
import { Skeleton } from "@/components/ui/Loading";
import { useTool } from "@/lib/hooks";
import type { JsonSchema } from "@/lib/types";

function typeOf(s: JsonSchema): string {
  if (typeof s.type === "string") return s.type;
  if (Array.isArray(s.anyOf)) {
    const parts = s.anyOf.map((a) => (typeof a.type === "string" ? a.type : "?")).filter((t) => t !== "null");
    return `${parts.join(" | ")}${s.anyOf.some((a) => a.type === "null") ? " | null" : ""}`;
  }
  if (Array.isArray(s.enum)) return "enum";
  return "any";
}

function constraints(s: JsonSchema): string[] {
  const out: string[] = [];
  if (s.minimum != null) out.push(`≥ ${s.minimum}`);
  if (s.maximum != null) out.push(`≤ ${s.maximum}`);
  if (s.exclusiveMinimum != null) out.push(`> ${s.exclusiveMinimum}`);
  if (s.exclusiveMaximum != null) out.push(`< ${s.exclusiveMaximum}`);
  if (s.minLength != null) out.push(`min length ${s.minLength}`);
  if (s.maxLength != null) out.push(`max length ${s.maxLength}`);
  if (Array.isArray(s.enum)) out.push(s.enum.map((e) => JSON.stringify(e)).join(" | "));
  if (s.default !== undefined) out.push(`default ${JSON.stringify(s.default)}`);
  return out;
}

/** Arguments schema as a field table. Arguments travel as JSON and are validated by the tool's model. */
export function SchemaView({ schema }: { schema: JsonSchema }) {
  const props = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const names = Object.keys(props);
  if (names.length === 0) return <div className="mono text-[11px] text-faint">No arguments.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table-grid">
        <thead>
          <tr>
            <th>Argument</th>
            <th>Type</th>
            <th>Constraints</th>
          </tr>
        </thead>
        <tbody>
          {names.map((n) => {
            const p = props[n];
            const req = required.has(n);
            return (
              <tr key={n}>
                <td>
                  <span className="mono text-[11.5px] text-ink">{n}</span>
                  {req ? <span className="mono ml-1 text-[10px] text-amber">required</span> : <span className="mono ml-1 text-[10px] text-faint">optional</span>}
                  {p.description ? <div className="text-[11px] leading-snug text-muted">{p.description}</div> : null}
                </td>
                <td className="mono text-[11px] text-sev4">{typeOf(p)}</td>
                <td className="mono text-[10.5px] text-muted">{constraints(p).join(" · ") || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ToolDrawer({ name, onClose }: { name: string | null; onClose: () => void }) {
  const q = useTool(name);
  const t = q.data;
  const dangerous = t?.category === "dangerous";

  return (
    <Drawer
      open={name != null}
      onClose={onClose}
      width={640}
      kicker={t ? `${t.category} · v${t.version}` : "tool"}
      title={<span className="mono">{name ?? ""}</span>}
    >
      {q.error ? <ProblemBanner error={q.error} onRetry={() => void q.refetch()} /> : null}
      {q.isPending ? <Skeleton rows={8} /> : null}
      {t ? (
        <div className="space-y-5">
          {dangerous ? (
            <div className="rounded-sm border border-sev1/60 bg-sev1/[0.07] px-3 py-2" style={{ borderLeftWidth: 3 }}>
              <div className="micro-mono !text-sev1">runtime invariant</div>
              <div className="mt-0.5 text-[12.5px] text-ink">This tool is registered so it can be refused. It is never executable, by any actor, under any policy.</div>
            </div>
          ) : null}
          {t.category === "mutating" ? (
            <div className="rounded-sm border border-sev2/50 bg-sev2/[0.06] px-3 py-2" style={{ borderLeftWidth: 3 }}>
              <div className="micro-mono !text-sev2">runtime invariant</div>
              <div className="mt-0.5 text-[12.5px] text-ink">Mutating tools cannot run inside the agent loop. Only the Temporal workflow may execute this, after policy and approval.</div>
            </div>
          ) : null}

          <p className="text-[12.5px] leading-relaxed text-ink/90">{t.description}</p>

          <div className="flex flex-wrap items-center gap-2">
            <CategoryChip category={t.category} />
            <RiskChip risk={t.risk} />
            <Chip tone={t.enabled ? "ok" : "danger"} dot size="xs">
              {t.enabled ? "enabled" : "disabled"}
            </Chip>
            <Chip tone={t.idempotent ? "ok" : "warn"} size="xs" mono>
              {t.idempotent ? "idempotent" : "not idempotent"}
            </Chip>
            {t.produces_evidence ? (
              <Chip tone="info" size="xs" mono>
                produces evidence
              </Chip>
            ) : null}
          </div>

          <KeyValue
            columns={2}
            items={[
              { k: "Timeout", v: `${t.timeout_seconds}s`, mono: true },
              { k: "Retry", v: `${t.retry.max_attempts} attempts · backoff ${t.retry.initial_backoff_seconds}s ×${t.retry.backoff_multiplier} (max ${t.retry.max_backoff_seconds}s)`, mono: true },
              { k: "Min severity", v: t.min_severity ?? "any", mono: true },
              { k: "Environments", v: t.allowed_environments.slice().sort().join(", ") || "none", mono: true },
              { k: "Capabilities", v: t.capabilities.length ? t.capabilities.slice().sort().join(", ") : "—", mono: true, span: 2 },
              ...(t.verification_metrics.length ? [{ k: "Verification metrics", v: t.verification_metrics.slice().sort().join(", "), mono: true, span: 2 as const }] : []),
            ]}
          />

          <div className="border-t border-hairline pt-3">
            <div className="micro mb-1.5">Arguments schema</div>
            <SchemaView schema={t.arguments_schema} />
          </div>

          <div className="border-t border-hairline pt-3">
            <div className="micro mb-1.5">Used in</div>
            {Object.keys(t.used_in).length === 0 ? (
              <div className="text-[11.5px] text-faint">No flow pack allows this tool.</div>
            ) : (
              <ul className="space-y-1.5">
                {Object.entries(t.used_in)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([ref, phases]) => (
                    <li key={ref} className="flex flex-wrap items-baseline gap-1.5">
                      <span className="mono text-[11.5px] text-amber">{ref}</span>
                      {phases
                        .slice()
                        .sort()
                        .map((p) => (
                          <Tag key={p} accent={p === "<remediation>"}>
                            {p}
                          </Tag>
                        ))}
                    </li>
                  ))}
              </ul>
            )}
          </div>

          <details className="border-t border-hairline pt-3">
            <summary className="micro cursor-pointer hover:text-ink">Raw schema</summary>
            <JsonView value={t.arguments_schema} className="mt-2" />
          </details>
        </div>
      ) : null}
    </Drawer>
  );
}
