"use client";

import { useGlobalStream } from "@/components/shell/GlobalStream";
import { IconExternal } from "@/components/shell/Icons";
import { Chip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { KeyValue } from "@/components/ui/KeyValue";
import { LiveDot } from "@/components/ui/LiveDot";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section, Stat } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { RelativeTime } from "@/components/ui/Time";
import { apiDocsUrl, config } from "@/lib/config";
import { humanize } from "@/lib/format";
import { useHealth, useMe, useNotifications, useReady, useSystemInfo } from "@/lib/hooks";

interface LinkDef {
  label: string;
  href: string;
  note: string;
}

const LINKS: LinkDef[] = [
  { label: "API + OpenAPI docs", href: apiDocsUrl(), note: "FastAPI control plane" },
  { label: "Temporal UI", href: config.temporalUiUrl, note: "workflow histories, exactly-once proof" },
  { label: "Grafana", href: config.grafanaUrl, note: "dashboards (observability profile)" },
  { label: "Prometheus", href: config.prometheusUrl, note: "raw runtime metrics" },
];

export default function SystemPage() {
  const info = useSystemInfo();
  const ready = useReady();
  const health = useHealth();
  const me = useMe();
  const notifications = useNotifications(30);
  const stream = useGlobalStream();

  const checks = Object.entries(ready.data?.checks ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const readyOk = ready.data?.status === "ready";

  return (
    <div>
      <PageHeader
        kicker="System"
        title="Control plane status"
        meta={
          <>
            <span className="mono">{info.data ? `aegis ${info.data.version}` : "…"}</span>
            <span className="mono">{info.data ? `env ${info.data.environment}` : ""}</span>
            <span className="mono text-faint">api {config.apiUrl}</span>
          </>
        }
      />

      <div className="grid grid-cols-2 border-b border-hairline bg-panel md:grid-cols-5">
        <Stat
          label="Readiness"
          value={<span className="text-[15px]">{ready.isError ? "unreachable" : readyOk ? "ready" : ready.data ? "not ready" : "…"}</span>}
          tone={ready.isError || (ready.data && !readyOk) ? "#ff4d4f" : readyOk ? "#3ddc97" : undefined}
          sub={`${checks.filter(([, c]) => c.ok).length}/${checks.length} checks ok`}
        />
        <Stat label="Health" value={<span className="text-[15px]">{health.data?.status ?? "—"}</span>} sub={health.data ? `version ${health.data.version}` : undefined} />
        <Stat label="Event stream" value={<span className="text-[15px]">{stream.status}</span>} tone={stream.status === "open" ? "#3ddc97" : "#ff8a3d"} sub={`${stream.receivedCount} events received`} />
        <Stat label="Registry" value={info.data ? `${info.data.tools}` : "—"} sub={info.data ? `tools · ${info.data.policy_rules} policy rules` : undefined} />
        <Stat
          label="Model"
          value={<span className="mono text-[13px]">{info.data?.llm.reasoner ?? "—"}</span>}
          tone={info.data?.llm.healthy ? "#3ddc97" : "#ff8a3d"}
          sub={info.data ? `${info.data.llm.provider} · fast ${info.data.llm.fast}` : undefined}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2">
        <div className="border-r border-hairline">
          <Section title="Readiness checks" meta={ready.data ? `status ${ready.data.status}` : undefined} flush>
            {ready.isError ? (
              <ProblemBanner error={ready.error} compact className="m-3" onRetry={() => void ready.refetch()} />
            ) : checks.length === 0 ? (
              <Skeleton rows={4} />
            ) : (
              <ul>
                {checks.map(([name, c]) => {
                  const extras = Object.entries(c).filter(([k]) => k !== "ok");
                  return (
                    <li key={name} className="border-b border-hairline px-4 py-2 last:border-b-0">
                      <div className="flex items-center gap-2">
                        <LiveDot tone={c.ok ? "ok" : "danger"} />
                        <span className="mono text-[12px] text-ink">{name}</span>
                        <Chip tone={c.ok ? "ok" : "danger"} size="xs" mono className="ml-auto">
                          {c.ok ? "ok" : "failing"}
                        </Chip>
                      </div>
                      {extras.length ? (
                        <div className="mono mt-1 flex flex-wrap gap-x-3 pl-4 text-[10.5px] text-muted">
                          {extras.map(([k, v]) => (
                            <span key={k}>
                              <span className="text-faint">{k}=</span>
                              {typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? String(v) : JSON.stringify(v)}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>

          <Section title="System info" flush>
            {info.error ? (
              <ProblemBanner error={info.error} compact className="m-3" />
            ) : info.data ? (
              <div className="px-4 py-3">
                <KeyValue
                  columns={2}
                  items={[
                    { k: "Version", v: info.data.version, mono: true },
                    { k: "Environment", v: info.data.environment, mono: true },
                    { k: "Temporal", v: info.data.temporal ? "connected" : "not configured", mono: true },
                    { k: "Telemetry", v: info.data.telemetry_provider, mono: true },
                    { k: "LLM provider", v: info.data.llm.provider, mono: true },
                    {
                      k: "LLM health",
                      v: (
                        <Chip tone={info.data.llm.healthy ? "ok" : "warn"} dot size="xs">
                          {info.data.llm.healthy ? "healthy" : "unavailable — deterministic planner"}
                        </Chip>
                      ),
                    },
                    { k: "Reasoner model", v: info.data.llm.reasoner, mono: true },
                    { k: "Fast model", v: info.data.llm.fast, mono: true },
                    {
                      k: "Flow packs",
                      v: (
                        <span className="flex flex-wrap gap-1">
                          {info.data.flows
                            .slice()
                            .sort()
                            .map((f) => (
                              <Tag key={f}>{f}</Tag>
                            ))}
                        </span>
                      ),
                      span: 2,
                    },
                  ]}
                />
              </div>
            ) : (
              <Skeleton rows={4} />
            )}
          </Section>

          <Section title="Principal" flush>
            {me.data ? (
              <div className="px-4 py-3">
                <KeyValue
                  columns={2}
                  items={[
                    { k: "Id", v: me.data.id, mono: true },
                    { k: "Display name", v: me.data.display_name ?? "—" },
                    {
                      k: "Roles",
                      v: (
                        <span className="flex flex-wrap gap-1">
                          {me.data.roles
                            .slice()
                            .sort()
                            .map((r) => (
                              <Tag key={r} accent={r === "admin"}>
                                {r}
                              </Tag>
                            ))}
                        </span>
                      ),
                    },
                    { k: "Auth mode", v: me.data.auth_mode, mono: true },
                  ]}
                />
                {me.data.auth_mode === "disabled" ? (
                  <p className="mt-2 text-[11.5px] leading-snug text-sev3">
                    Auth is disabled, so every caller is the local operator with the admin role. Set <span className="mono">AEGIS_API_AUTH_MODE</span> and send a key to enforce roles.
                  </p>
                ) : null}
                {!config.apiKey ? <p className="mono mt-1.5 text-[10.5px] text-faint">console is sending no X-Aegis-Key</p> : null}
              </div>
            ) : (
              <Skeleton rows={3} />
            )}
          </Section>
        </div>

        <div>
          <Section title="Links" flush>
            <ul>
              {LINKS.map((l) => (
                <li key={l.label} className="border-b border-hairline last:border-b-0">
                  <a href={l.href} target="_blank" rel="noreferrer" className="flex items-center gap-2 px-4 py-2.5 hover:bg-panel-2">
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 text-[12.5px] text-ink">
                        {l.label}
                        <IconExternal />
                      </span>
                      <span className="mono block truncate text-[10.5px] text-faint">{l.href}</span>
                    </span>
                    <span className="ml-auto shrink-0 text-[11px] text-muted">{l.note}</span>
                  </a>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Console build" flush>
            <div className="px-4 py-3">
              <KeyValue
                columns={2}
                items={[
                  { k: "API base URL", v: config.apiUrl, mono: true, span: 2 },
                  { k: "API key", v: config.apiKey ? "configured" : "none", mono: true },
                  { k: "Temporal namespace", v: config.temporalNamespace, mono: true },
                  { k: "Stream state", v: `${stream.status}${stream.attempts ? ` · ${stream.attempts} retries` : ""}`, mono: true },
                  { k: "Events received", v: String(stream.receivedCount), mono: true },
                ]}
              />
              <p className="mt-2 text-[11.5px] leading-snug text-muted">
                Values come from <span className="mono">NEXT_PUBLIC_*</span> and are inlined at build time, so changing them needs a rebuild of the console image.
              </p>
            </div>
          </Section>

          <Section title="Notifications" meta={notifications.data ? `${notifications.data.length}` : undefined} flush>
            {notifications.error ? (
              <ProblemBanner error={notifications.error} compact className="m-3" />
            ) : notifications.isPending ? (
              <Skeleton rows={4} />
            ) : (notifications.data ?? []).length === 0 ? (
              <EmptyState compact title="No notifications" />
            ) : (
              <ul>
                {(notifications.data ?? []).map((n) => (
                  <li key={n.id} className="border-b border-hairline px-4 py-2 last:border-b-0">
                    <div className="flex items-center gap-2">
                      <LiveDot tone={n.read ? "neutral" : "live"} pulse={!n.read} size={5} />
                      <span className="mono text-[10.5px] tracking-[0.06em] text-muted uppercase">{humanize(n.kind)}</span>
                      <RelativeTime iso={n.at} className="ml-auto text-[10.5px] text-faint" />
                    </div>
                    <div className="mt-0.5 text-[12px] text-ink">{n.title}</div>
                    {n.body ? <div className="text-[11px] leading-snug text-muted">{n.body}</div> : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}
