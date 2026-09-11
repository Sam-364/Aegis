"use client";

import { useState } from "react";
import { Chip, ExecStatusChip, RiskChip, Tag } from "@/components/ui/Chip";
import { EmptyState } from "@/components/ui/EmptyState";
import { GateSequence } from "@/components/ui/GateSequence";
import { JsonView } from "@/components/ui/JsonView";
import { Meter } from "@/components/ui/Meter";
import { Skeleton } from "@/components/ui/Loading";
import { AMBER, DANGER, hexWithAlpha, INFO, MUTED, OK, TONE_COLOR, WARN } from "@/lib/colors";
import { asArray, asNumber, asString, fmtCompact, fmtMs, fmtScore, fmtTime, humanize, isRecord } from "@/lib/format";
import type { AgentStep, AgentStepKind, AuthorizationDecision, BudgetUsage, ExecutionBudget, GateCheck, HypothesisPayload } from "@/lib/types";

// ---- payload readers (step input/output are untyped records over the wire) --------------------

function readDecision(output: Record<string, unknown>): AuthorizationDecision | null {
  const d = output.decision;
  if (!isRecord(d)) return null;
  const checks = asArray(d.checks)
    .filter(isRecord)
    .map<GateCheck>((c) => ({ name: asString(c.name), passed: c.passed === true, detail: asString(c.detail) }));
  return {
    request_id: asString(d.request_id),
    tool_name: asString(d.tool_name),
    effect: (asString(d.effect, "deny") as AuthorizationDecision["effect"]) ?? "deny",
    allowed: d.allowed === true,
    checks,
    denial_code: typeof d.denial_code === "string" ? d.denial_code : null,
    reason: asString(d.reason),
    matched_policy: typeof d.matched_policy === "string" ? d.matched_policy : null,
    decided_at: asString(d.decided_at),
  };
}

function readHypotheses(output: Record<string, unknown>): HypothesisPayload[] {
  return asArray(output.hypotheses)
    .filter(isRecord)
    .map((h) => ({
      hypothesis_id: asString(h.hypothesis_id),
      statement: asString(h.statement),
      category: asString(h.category),
      root_cause_service: typeof h.root_cause_service === "string" ? h.root_cause_service : null,
      status: asString(h.status),
      confidence: asNumber(h.confidence) ?? 0,
      score: isRecord(h.score)
        ? {
            evidence_strength: asNumber(h.score.evidence_strength) ?? 0,
            temporal_alignment: asNumber(h.score.temporal_alignment) ?? 0,
            dependency_alignment: asNumber(h.score.dependency_alignment) ?? 0,
            historical_similarity: asNumber(h.score.historical_similarity) ?? 0,
            contradiction_penalty: asNumber(h.score.contradiction_penalty) ?? 0,
            validation_bonus: asNumber(h.score.validation_bonus) ?? 0,
            total: asNumber(h.score.total) ?? 0,
            explanation: asArray<string>(h.score.explanation).filter((s): s is string => typeof s === "string"),
          }
        : { evidence_strength: 0, temporal_alignment: 0, dependency_alignment: 0, historical_similarity: 0, contradiction_penalty: 0, validation_bonus: 0, total: 0, explanation: [] },
      supporting: asNumber(h.supporting) ?? 0,
      contradicting: asNumber(h.contradicting) ?? 0,
    }));
}

function toolArgs(input: Record<string, unknown>): Record<string, unknown> {
  return isRecord(input.arguments) ? input.arguments : {};
}

function argsLine(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" ");
}

function parseArgsJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

const KIND_COLOR: Record<AgentStepKind, string> = {
  proposal: AMBER,
  fallback: WARN,
  authorization: INFO,
  tool_execution: OK,
  hypothesis_update: INFO,
  remediation_plan: WARN,
  decision: MUTED,
  observation: MUTED,
  evidence_update: INFO,
};

const KIND_LABEL: Record<AgentStepKind, string> = {
  proposal: "model proposes",
  fallback: "planner proposes",
  authorization: "runtime authorizes",
  tool_execution: "tool executes",
  hypothesis_update: "runtime scores",
  remediation_plan: "remediation planned",
  decision: "runtime decides",
  observation: "observation",
  evidence_update: "evidence update",
};

/** One-line summary rendered on the collapsed row. */
function stepSummary(step: AgentStep): string {
  switch (step.kind) {
    case "proposal":
    case "fallback": {
      const p = isRecord(step.output.proposal) ? step.output.proposal : null;
      const action = p ? asString(p.action) : "";
      const call = p && isRecord(p.tool_call) ? asString(p.tool_call.tool_name) : "";
      return [action, call].filter(Boolean).join(" · ");
    }
    case "authorization": {
      const d = readDecision(step.output);
      return d ? (d.allowed ? `${d.tool_name} allowed` : `${d.tool_name} denied · ${d.denial_code ?? d.reason}`) : "";
    }
    case "tool_execution":
      return asString(step.output.summary) || asString(step.output.status);
    case "hypothesis_update": {
      const hs = readHypotheses(step.output);
      return hs.length ? `${hs.length} scored · top ${fmtScore(hs[0].confidence)}` : "";
    }
    case "remediation_plan": {
      const plan = isRecord(step.output.action_plan) ? step.output.action_plan : null;
      return plan ? `${asString(plan.tool_name)} · risk ${asString(plan.risk)}` : "";
    }
    case "decision": {
      const metrics = asArray<string>(step.output.metrics).filter((m): m is string => typeof m === "string");
      if (metrics.length) return `watching ${metrics.join(", ")}`;
      if ("exit_met" in step.output) {
        const unsat = asArray<string>(step.output.unsatisfied).filter((m): m is string => typeof m === "string");
        return step.output.exit_met === true ? "exit conditions met" : `unsatisfied: ${unsat.join(", ") || "—"}`;
      }
      return asString(step.output.observation);
    }
    default:
      return "";
  }
}

function ProposalBody({ step }: { step: AgentStep }) {
  const p = isRecord(step.output.proposal) ? step.output.proposal : null;
  if (!p) return <JsonView value={step.output} />;
  const call = isRecord(p.tool_call) ? p.tool_call : null;
  const rem = isRecord(p.remediation) ? p.remediation : null;
  const recovered = step.output.recovered === true;
  return (
    <div className="space-y-2.5">
      {recovered ? <Chip tone="warn" size="xs">recovered from malformed model output</Chip> : null}
      {asString(p.observation) ? (
        <div>
          <div className="micro mb-0.5">Observation</div>
          <p className="text-[12px] leading-relaxed text-ink/90">{asString(p.observation)}</p>
        </div>
      ) : null}
      {asString(p.rationale) ? (
        <div>
          <div className="micro mb-0.5">Rationale</div>
          <p className="text-[12px] leading-relaxed text-ink/90">{asString(p.rationale)}</p>
        </div>
      ) : null}
      {call ? (
        <div className="rounded-sm border border-hairline bg-canvas px-2.5 py-2">
          <div className="micro mb-1">Requested tool call — a request, not an execution</div>
          <div className="mono text-[12px] text-ink">
            {asString(call.tool_name)}
            <span className="text-muted">({argsLine(isRecord(parseArgsJson(asString(call.arguments_json, "{}"))) ? (parseArgsJson(asString(call.arguments_json, "{}")) as Record<string, unknown>) : {})})</span>
          </div>
          {asString(call.purpose) ? <div className="mt-1 text-[11.5px] text-muted">{asString(call.purpose)}</div> : null}
          {asString(call.expectation) ? (
            <div className="mt-0.5 text-[11.5px] text-muted">
              <span className="micro mr-1">expects</span>
              {asString(call.expectation)}
            </div>
          ) : null}
        </div>
      ) : null}
      {rem ? (
        <div className="rounded-sm border border-sev2/40 bg-sev2/[0.06] px-2.5 py-2">
          <div className="micro mb-1 !text-sev2">Proposed remediation — requires policy and human approval</div>
          <div className="mono text-[12px] text-ink">
            {asString(rem.tool_name)}
            <span className="text-muted">({argsLine(isRecord(parseArgsJson(asString(rem.arguments_json, "{}"))) ? (parseArgsJson(asString(rem.arguments_json, "{}")) as Record<string, unknown>) : {})})</span>
          </div>
          {asString(rem.reason) ? <div className="mt-1 text-[11.5px] text-muted">{asString(rem.reason)}</div> : null}
          {asString(rem.expected_effect) ? <div className="mt-0.5 text-[11.5px] text-muted">expected: {asString(rem.expected_effect)}</div> : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-hairline pt-1.5">
        {step.model ? <span className="mono text-[10.5px] text-muted">model {step.model}</span> : null}
        <span className="mono text-[10.5px] text-muted">
          tokens {fmtCompact(step.tokens_in)} in / {fmtCompact(step.tokens_out)} out
        </span>
        <span className="mono text-[10.5px] text-muted">latency {fmtMs(step.latency_ms)}</span>
        {isRecord(step.input) ? <span className="mono text-[10.5px] text-faint">iteration {asNumber(step.input.iteration) ?? "—"}</span> : null}
      </div>
    </div>
  );
}

function AuthorizationBody({ step }: { step: AgentStep }) {
  const d = readDecision(step.output);
  if (!d) return <JsonView value={step.output} />;
  return (
    <div className="space-y-2">
      <div className="mono text-[12px] text-ink">
        {d.tool_name} <span className="text-muted">{argsLine(toolArgs(step.input))}</span>
      </div>
      <GateSequence checks={d.checks} denialCode={d.denial_code} reason={d.reason} />
      <div className="flex flex-wrap items-center gap-2 border-t border-hairline pt-1.5">
        <Chip tone={d.allowed ? "ok" : "danger"} dot size="xs">
          {d.allowed ? "allowed" : "denied"}
        </Chip>
        <span className="mono text-[10.5px] text-muted">effect {d.effect}</span>
        {d.matched_policy ? <span className="mono text-[10.5px] text-muted">policy {d.matched_policy}</span> : null}
      </div>
    </div>
  );
}

function ToolExecutionBody({ step }: { step: AgentStep }) {
  const status = asString(step.output.status, "unknown");
  const evidenceIds = asArray<string>(step.output.evidence_ids).filter((s): s is string => typeof s === "string");
  return (
    <div className="space-y-2">
      <div className="mono text-[12px] text-ink">
        {asString(step.input.tool)} <span className="text-muted">{argsLine(toolArgs(step.input))}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <ExecStatusChip status={status} size="xs" />
        <span className="mono text-[10.5px] text-muted">{fmtMs(asNumber(step.output.duration_ms))}</span>
        {evidenceIds.length ? <span className="mono text-[10.5px] text-ok">+{evidenceIds.length} evidence</span> : null}
      </div>
      {asString(step.output.summary) ? <p className="text-[12px] leading-snug text-ink/90">{asString(step.output.summary)}</p> : null}
    </div>
  );
}

function HypothesisUpdateBody({ step }: { step: AgentStep }) {
  const hs = readHypotheses(step.output);
  if (hs.length === 0) return <JsonView value={step.output} />;
  return (
    <ul className="space-y-2">
      {hs.map((h, i) => (
        <li key={h.hypothesis_id || i} className="border-b border-hairline pb-2 last:border-b-0 last:pb-0">
          <div className="flex items-start gap-2">
            <span className="min-w-0 flex-1 text-[12px] leading-snug text-ink/90">{h.statement}</span>
            <span className="mono shrink-0 text-[13px]" style={{ color: h.confidence >= 0.8 ? OK : h.confidence >= 0.55 ? AMBER : WARN }}>
              {fmtScore(h.confidence)}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Tag>{humanize(h.category)}</Tag>
            {h.root_cause_service ? <Tag accent>{h.root_cause_service}</Tag> : null}
            <span className="mono text-[10.5px] text-muted">{h.status}</span>
            <span className="mono text-[10.5px] text-ok">+{h.supporting}</span>
            <span className="mono text-[10.5px] text-sev1">−{h.contradicting}</span>
          </div>
          {h.score.explanation.length ? (
            <ul className="mt-1 space-y-0.5">
              {h.score.explanation.slice(0, 4).map((line, j) => (
                <li key={j} className="text-[11px] leading-snug text-muted">
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function RemediationPlanBody({ step }: { step: AgentStep }) {
  const plan = isRecord(step.output.action_plan) ? step.output.action_plan : null;
  if (!plan) return <JsonView value={step.output} />;
  const args = isRecord(plan.arguments) ? plan.arguments : {};
  return (
    <div className="space-y-2">
      <div className="mono text-[12.5px] text-ink">
        {asString(plan.tool_name)} <span className="text-muted">{argsLine(args)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <RiskChip risk={asString(plan.risk, "none")} size="xs" />
        <span className="mono text-[10.5px] text-muted">status {asString(plan.status)}</span>
        <span className="mono text-[10.5px] text-faint">key {asString(plan.idempotency_key).slice(0, 12)}</span>
      </div>
      {asString(plan.reason) ? <p className="text-[12px] leading-snug text-ink/90">{asString(plan.reason)}</p> : null}
      {asString(plan.expected_effect) ? <p className="text-[11.5px] leading-snug text-muted">expected: {asString(plan.expected_effect)}</p> : null}
    </div>
  );
}

function DecisionBody({ step }: { step: AgentStep }) {
  const metrics = asArray<string>(step.output.metrics).filter((m): m is string => typeof m === "string");
  const unsatisfied = asArray<string>(step.output.unsatisfied).filter((m): m is string => typeof m === "string");
  const observation = asString(step.output.observation);
  return (
    <div className="space-y-1.5">
      {observation ? <p className="text-[12px] leading-snug text-ink/90">{observation}</p> : null}
      {metrics.length ? (
        <div className="flex flex-wrap items-center gap-1">
          <span className="micro mr-1">metrics</span>
          {metrics.map((m) => (
            <Tag key={m}>{m}</Tag>
          ))}
        </div>
      ) : null}
      {"exit_met" in step.output ? (
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={step.output.exit_met === true ? "ok" : "warn"} dot size="xs">
            {step.output.exit_met === true ? "exit conditions met" : "exit conditions unmet"}
          </Chip>
          {unsatisfied.map((u) => (
            <span key={u} className="mono text-[10.5px] text-sev2">
              {u}
            </span>
          ))}
        </div>
      ) : null}
      {!observation && !metrics.length && !("exit_met" in step.output) ? <JsonView value={step.output} /> : null}
    </div>
  );
}

function StepBody({ step }: { step: AgentStep }) {
  switch (step.kind) {
    case "proposal":
    case "fallback":
      return <ProposalBody step={step} />;
    case "authorization":
      return <AuthorizationBody step={step} />;
    case "tool_execution":
      return <ToolExecutionBody step={step} />;
    case "hypothesis_update":
      return <HypothesisUpdateBody step={step} />;
    case "remediation_plan":
      return <RemediationPlanBody step={step} />;
    case "decision":
      return <DecisionBody step={step} />;
    default:
      return <JsonView value={step.output} />;
  }
}

export function StepRow({ step, defaultOpen = false }: { step: AgentStep; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const color = KIND_COLOR[step.kind] ?? MUTED;
  const summary = stepSummary(step);
  return (
    <li className="border-b border-hairline last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} className="grid w-full grid-cols-[22px_1fr] items-start gap-x-2 px-3 py-1.5 text-left hover:bg-panel-2" aria-expanded={open}>
        <span className="mono pt-[2px] text-[10px] text-faint tabular-nums">{String(step.seq).padStart(2, "0")}</span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="mono text-[10px] tracking-[0.08em] uppercase" style={{ color }}>
              {KIND_LABEL[step.kind] ?? step.kind}
            </span>
            <span className="mono text-[10px] text-faint">{step.node}</span>
            <span className="mono ml-auto text-[10px] text-faint">{fmtTime(step.at)}</span>
          </span>
          <span className="mt-0.5 block truncate text-[12px] text-ink">{step.title}</span>
          {summary ? <span className="block truncate text-[11px] text-muted">{summary}</span> : null}
        </span>
      </button>
      {open ? (
        <div className="border-l-2 px-3 py-2.5 pl-[26px]" style={{ borderLeftColor: hexWithAlpha(color, 0.5) }}>
          <StepBody step={step} />
        </div>
      ) : null}
    </li>
  );
}

export function StepList({ steps, loading = false, defaultOpenLast = false }: { steps: AgentStep[]; loading?: boolean; defaultOpenLast?: boolean }) {
  if (loading) return <Skeleton rows={5} />;
  if (steps.length === 0) return <EmptyState compact title="No steps recorded" hint="Steps appear as the agent graph runs: propose → authorize → execute → score → decide." />;
  const ordered = steps.slice().sort((a, b) => a.seq - b.seq);
  return (
    <ul>
      {ordered.map((s, i) => (
        <StepRow key={s.id} step={s} defaultOpen={defaultOpenLast && i === ordered.length - 1} />
      ))}
    </ul>
  );
}

const BUDGET_FIELDS: { usage: keyof BudgetUsage; budget: keyof ExecutionBudget; label: string }[] = [
  { usage: "iterations", budget: "max_iterations", label: "iterations" },
  { usage: "tool_calls", budget: "max_tool_calls", label: "tool calls" },
  { usage: "llm_calls", budget: "max_llm_calls", label: "llm calls" },
  { usage: "llm_tokens", budget: "max_llm_tokens", label: "llm tokens" },
  { usage: "runtime_seconds", budget: "max_runtime_seconds", label: "runtime s" },
];

/** Usage against budget. The runtime stops the agent at the ceiling; this shows how close it got. */
export function BudgetBars({ budget, usage, className = "" }: { budget: ExecutionBudget; usage: BudgetUsage; className?: string }) {
  return (
    <div className={`space-y-1 ${className}`}>
      {BUDGET_FIELDS.map((f) => {
        const used = usage[f.usage];
        const max = budget[f.budget];
        const ratio = max > 0 ? used / max : 0;
        const color = ratio >= 1 ? DANGER : ratio >= 0.8 ? WARN : TONE_COLOR.info;
        return (
          <div key={f.label} className="flex items-center gap-2">
            <span className="micro w-20 shrink-0">{f.label}</span>
            <Meter value={Math.min(1, ratio)} color={color} height={4} showValue={false} className="flex-1" />
            <span className="mono w-[74px] shrink-0 text-right text-[10.5px] text-muted">
              {fmtCompact(used)} / {fmtCompact(max)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
