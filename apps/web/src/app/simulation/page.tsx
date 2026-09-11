"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Chip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { LiveDot } from "@/components/ui/LiveDot";
import { ProblemBanner } from "@/components/ui/Problem";
import { PageHeader, Section, Stat } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { healthColor, saturationColor } from "@/lib/colors";
import { fmtDuration, fmtMs, fmtPct, humanize } from "@/lib/format";
import { useFaults, useScenarios, useSimulationMutations, useSimulationState } from "@/lib/hooks";
import type { Scenario } from "@/lib/types";

const ADVANCE_STEPS = [30, 60, 300];

function expectation(s: Scenario): { label: string; tone: "ok" | "warn" | "danger" | "neutral" } {
  if (s.expect_escalation) return { label: "expects escalation", tone: "danger" };
  if (s.expect_no_action) return { label: "expects no action", tone: "warn" };
  return { label: "expects remediation", tone: "ok" };
}

function ScenarioCard({ s, onInject, injecting, disabled }: { s: Scenario; onInject: () => void; injecting: boolean; disabled: boolean }) {
  const exp = expectation(s);
  return (
    <div className="flex min-w-0 flex-col border-r border-b border-hairline">
      <header className="flex h-7 items-center gap-2 border-b border-hairline bg-panel-2 px-2.5">
        <span className="mono text-[11px] text-ink">{s.id}</span>
        <Chip tone={exp.tone} size="xs" mono className="ml-auto">
          {exp.label}
        </Chip>
      </header>
      <div className="flex-1 space-y-2.5 px-2.5 py-2.5">
        <div>
          <div className="text-[12.5px] font-medium text-ink">{s.title}</div>
          <p className="mt-0.5 text-[11.5px] leading-snug text-muted">{s.description}</p>
        </div>
        <div className="mono flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] text-faint">
          <span>fault {s.fault_type}</span>
          {s.self_resolving_seconds != null ? <span>self-resolves in {fmtDuration(s.self_resolving_seconds)}</span> : null}
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <div>
            <div className="micro mb-0.5">Root cause</div>
            <div className="flex flex-wrap items-center gap-1">
              {s.root_cause_service ? <Tag accent>{s.root_cause_service}</Tag> : <span className="mono text-[10.5px] text-faint">none</span>}
              <Tag>{humanize(s.root_cause_category)}</Tag>
            </div>
          </div>
          <div>
            <div className="micro mb-0.5">Detection hints</div>
            <div className="mono text-[10.5px] text-muted">{s.detection_hint_metrics.join(", ") || "—"}</div>
          </div>
        </div>
        {s.expected_symptoms.length ? (
          <div>
            <div className="micro mb-0.5">Expected symptoms</div>
            <ul className="space-y-0.5">
              {s.expected_symptoms.map((sym, i) => (
                <li key={i} className="text-[11px] leading-snug text-muted">
                  {sym}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-x-3">
          <div>
            <div className="micro mb-0.5 !text-ok">Correct</div>
            {s.correct_remediations.length === 0 ? (
              <span className="mono text-[10.5px] text-faint">none available</span>
            ) : (
              <ul className="space-y-0.5">
                {s.correct_remediations.map((r, i) => (
                  <li key={i} className="mono text-[10.5px] text-ok">
                    {r.action}({r.target})
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <div className="micro mb-0.5 !text-sev1">Wrong</div>
            {s.incorrect_remediations.length === 0 ? (
              <span className="mono text-[10.5px] text-faint">—</span>
            ) : (
              <ul className="space-y-0.5">
                {s.incorrect_remediations.map((r, i) => (
                  <li key={i} className="mono text-[10.5px] text-sev1">
                    {r.action}({r.target})
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
      <footer className="border-t border-hairline px-2.5 py-2">
        <Button size="sm" variant="primary" onClick={onInject} loading={injecting} disabled={disabled} className="w-full justify-center">
          {disabled ? "already active" : "Inject fault"}
        </Button>
      </footer>
    </div>
  );
}

export default function SimulationPage() {
  const scenarios = useScenarios();
  const faults = useFaults(false, 5_000);
  const state = useSimulationState(5_000);
  const { inject, clear, advance, reset } = useSimulationMutations();
  const [seedInput, setSeedInput] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);

  const activeFaults = (faults.data ?? []).filter((f) => f.active);
  const clearedFaults = (faults.data ?? []).filter((f) => !f.active);
  const activeScenarioIds = new Set(activeFaults.map((f) => f.scenario_id));
  const services = Object.entries(state.data?.services ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const infra = Object.entries(state.data?.infra ?? {}).sort(([a], [b]) => a.localeCompare(b));

  const mutationError = inject.error ?? clear.error ?? advance.error ?? reset.error;

  return (
    <div>
      <PageHeader
        kicker="Simulation"
        title="Fault injection and time control"
        meta={
          <>
            <span className="mono">{scenarios.data ? `${scenarios.data.length} scenarios` : "…"}</span>
            <span className="mono flex items-center gap-1.5">
              <LiveDot tone={activeFaults.length > 0 ? "danger" : "ok"} pulse />
              {activeFaults.length > 0 ? `${activeFaults.length} active` : "clean"}
            </span>
            <span className="text-faint">
              The simulated distributed system is a separate package that cannot import the runtime, so the agent has no access to the ground truth shown on this page.
            </span>
          </>
        }
      />

      {mutationError ? <ProblemBanner error={mutationError} className="m-4" /> : null}

      <div className="grid grid-cols-2 border-b border-hairline bg-panel md:grid-cols-4">
        <Stat label="Sim time" value={<span className="text-[14px]">{state.data?.time ?? "—"}</span>} sub={state.data ? `tick ${state.data.tick_count}` : undefined} />
        <Stat label="Seed" value={state.data?.seed ?? "—"} sub="deterministic" />
        <Stat label="Active faults" value={activeFaults.length} tone={activeFaults.length > 0 ? "#ff4d4f" : undefined} />
        <Stat label="Components" value={services.length + infra.length} sub={`${services.length} services · ${infra.length} infra`} />
      </div>

      <Section
        title="Time control"
        meta="advance the simulated clock"
        actions={
          <div className="flex items-center gap-1.5">
            {ADVANCE_STEPS.map((s) => (
              <Button key={s} size="sm" onClick={() => advance.mutate(s)} loading={advance.isPending && advance.variables === s}>
                +{s < 60 ? `${s}s` : `${s / 60}m`}
              </Button>
            ))}
            <span className="mx-2 h-4 w-px bg-hairline" />
            <input value={seedInput} onChange={(e) => setSeedInput(e.target.value.replace(/[^0-9]/g, "").slice(0, 9))} placeholder="seed" className="field mono w-[76px]" />
            <Button
              size="sm"
              variant={confirmReset ? "danger-solid" : "danger"}
              loading={reset.isPending}
              onClick={() => {
                if (!confirmReset) {
                  setConfirmReset(true);
                  return;
                }
                reset.mutate(Number(seedInput) || 0);
                setConfirmReset(false);
              }}
            >
              {confirmReset ? "Confirm reset" : "Reset simulator"}
            </Button>
            {confirmReset ? (
              <Button size="sm" variant="ghost" onClick={() => setConfirmReset(false)}>
                cancel
              </Button>
            ) : null}
          </div>
        }
      >
        <p className="text-[11.5px] leading-snug text-muted">
          Advancing the clock makes the simulator tick without waiting in real time — useful to watch a fault develop or a self-resolving spike pass. Resetting rebuilds the whole simulated
          system from the seed and clears every fault; incidents already recorded by the runtime are untouched.
        </p>
      </Section>

      <Section title="Active faults" meta={`${activeFaults.length}`} live={activeFaults.length > 0} flush>
        {faults.error ? (
          <ProblemBanner error={faults.error} className="m-3" onRetry={() => void faults.refetch()} />
        ) : activeFaults.length === 0 ? (
          <EmptyState compact title="No faults injected" hint="Pick a scenario below. The detector should open an incident within about twenty seconds." />
        ) : (
          <ul>
            {activeFaults.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-2.5 last:border-b-0">
                <span className="mono text-[12px] text-sev1">{f.scenario_id}</span>
                <Chip tone="danger" size="xs" mono>
                  {f.fault_type}
                </Chip>
                <span className="mono text-[10.5px] text-muted">
                  {Object.entries(f.params)
                    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                    .join("  ")}
                </span>
                <span className="mono text-[10px] text-faint">
                  {f.id} · started {new Date(f.started_at * 1000).toLocaleTimeString()}
                  {f.duration_seconds != null ? ` · lasts ${fmtDuration(f.duration_seconds)}` : ""}
                </span>
                <Button size="sm" variant="danger" className="ml-auto" onClick={() => clear.mutate(f.id)} loading={clear.isPending && clear.variables === f.id}>
                  Clear fault
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Scenarios" meta={`${scenarios.data?.length ?? 0} · ground truth from src/aegis/simulator/faults.py`} flush>
        {scenarios.error ? (
          <ProblemBanner error={scenarios.error} className="m-3" onRetry={() => void scenarios.refetch()} />
        ) : scenarios.isPending ? (
          <Skeleton rows={6} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4">
            {(scenarios.data ?? []).map((s) => (
              <ScenarioCard
                key={s.id}
                s={s}
                onInject={() => inject.mutate({ scenarioId: s.id })}
                injecting={inject.isPending && inject.variables?.scenarioId === s.id}
                disabled={activeScenarioIds.has(s.id)}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title="Current state" meta={state.data ? `polled every 5s` : undefined} flush>
        {state.error ? (
          <ProblemBanner error={state.error} className="m-3" />
        ) : state.isPending ? (
          <Skeleton rows={6} />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2">
            <div className="overflow-x-auto border-r border-hairline">
              <table className="table-grid">
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Health</th>
                    <th className="text-right">p95</th>
                    <th className="text-right">Errors</th>
                    <th className="text-right">Replicas</th>
                    <th>Version</th>
                  </tr>
                </thead>
                <tbody>
                  {services.map(([name, s]) => (
                    <tr key={name}>
                      <td className="mono text-[11.5px]">{name}</td>
                      <td>
                        <span className="mono flex items-center gap-1.5 text-[11px]" style={{ color: healthColor(s.health) }}>
                          <LiveDot color={healthColor(s.health)} size={5} />
                          {s.up ? s.health : "down"}
                        </span>
                      </td>
                      <td className="mono text-right text-[11px] text-muted">{fmtMs(s.latency_p95_ms)}</td>
                      <td className="mono text-right text-[11px]" style={{ color: s.error_rate > 0.05 ? "#ff4d4f" : s.error_rate > 0.01 ? "#ff8a3d" : "#8a9099" }}>
                        {fmtPct(s.error_rate)}
                      </td>
                      <td className="mono text-right text-[11px] text-muted">{s.replicas}</td>
                      <td className="mono text-[11px] text-muted">{s.version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="overflow-x-auto">
              <table className="table-grid">
                <thead>
                  <tr>
                    <th>Infrastructure</th>
                    <th>Up</th>
                    <th className="text-right">Connections</th>
                    <th className="text-right">Saturation</th>
                  </tr>
                </thead>
                <tbody>
                  {infra.map(([name, s]) => (
                    <tr key={name}>
                      <td className="mono text-[11.5px]">{name}</td>
                      <td className="mono text-[11px] text-muted">{s.up ? "yes" : "no"}</td>
                      <td className="mono text-right text-[11px] text-muted">{s.connections}</td>
                      <td className="mono text-right text-[11px]" style={{ color: saturationColor(s.saturation) }}>
                        {fmtPct(s.saturation, 0)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Section>

      {clearedFaults.length > 0 ? (
        <Section title="Cleared faults" meta={`${clearedFaults.length}`} flush>
          <ul>
            {clearedFaults
              .slice()
              .sort((a, b) => (b.cleared_at ?? 0) - (a.cleared_at ?? 0))
              .slice(0, 20)
              .map((f) => (
                <li key={f.id} className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-1.5 last:border-b-0">
                  <span className="mono text-[11.5px] text-muted">{f.scenario_id}</span>
                  <span className="mono text-[10px] text-faint">{f.fault_type}</span>
                  <span className="mono ml-auto text-[10px] text-faint">
                    cleared {f.cleared_at != null ? new Date(f.cleared_at * 1000).toLocaleTimeString() : "—"}
                    {f.cleared_by ? ` by ${f.cleared_by}` : ""}
                  </span>
                </li>
              ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
