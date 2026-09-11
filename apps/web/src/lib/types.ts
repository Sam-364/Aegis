/**
 * Domain types for the Aegis control-plane API.
 *
 * Most routes are typed as `dict` in the OpenAPI document, so their exact shapes come from
 * docs/api/frontend-contract.md. Where the spec does carry a schema we alias the generated type.
 */
import type { components } from "@/lib/api-types";

// ---- generated aliases ---------------------------------------------------------------------

export type IncidentSummary = components["schemas"]["IncidentSummary"];
export type Principal = components["schemas"]["Principal"];
export type StatsResponse = components["schemas"]["StatsResponse"];
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

// ---- enums -----------------------------------------------------------------------------------

export type Environment = "development" | "staging" | "production";
export type Severity = components["schemas"]["Severity"];
export type IncidentStatus = components["schemas"]["IncidentStatus"];
export type ToolCategory = "read_only" | "diagnostic" | "mutating" | "dangerous";
export type RiskLevel = "none" | "low" | "medium" | "high" | "critical";
export type PolicyEffect = "allow" | "deny" | "require_approval";
export type ApprovalStatus = components["schemas"]["ApprovalStatus"];
export type HypothesisStatus = "proposed" | "testing" | "supported" | "confirmed" | "refuted" | "abandoned";
export type HypothesisCategory =
  | "resource_exhaustion"
  | "deployment_regression"
  | "dependency_failure"
  | "capacity"
  | "network"
  | "configuration"
  | "transient"
  | "unknown";
export type ActionPlanStatus =
  | "proposed"
  | "policy_denied"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "executing"
  | "executed"
  | "execution_failed"
  | "verified"
  | "verification_failed"
  | "rolled_back"
  | "superseded";
export type ExecutionStatus = "pending" | "running" | "succeeded" | "failed" | "timed_out" | "denied" | "skipped_duplicate";
export type EvidenceKind =
  | "signal"
  | "metric"
  | "log"
  | "trace"
  | "topology"
  | "deployment"
  | "health"
  | "diagnostic"
  | "memory"
  | "observation"
  | "action_result"
  | "verification";
export type RelationKind = "supports" | "contradicts" | "caused_by" | "depends_on" | "correlated_with" | "observed_on" | "resolved_by";
export type GraphNodeKind = "evidence" | "hypothesis" | "service" | "action";
export type AgentRunStatus = "running" | "completed" | "failed" | "budget_exhausted" | "cancelled";
export type TerminationReason =
  | "phase_complete"
  | "action_planned"
  | "no_action_required"
  | "escalate"
  | "budget_exhausted"
  | "llm_unavailable"
  | "incident_inactive"
  | "error";
export type AgentStepKind =
  | "proposal"
  | "fallback"
  | "authorization"
  | "tool_execution"
  | "hypothesis_update"
  | "remediation_plan"
  | "decision"
  | "observation"
  | "evidence_update";
export type SignalKind = "latency" | "error_rate" | "saturation" | "traffic" | "availability" | "resource";
export type ActorKind = "system" | "detector" | "agent" | "workflow" | "human" | "api";
export type Role = "viewer" | "operator" | "admin";
export type VerificationStatus = "pending" | "passed" | "failed" | "inconclusive";
export type HealthState = "healthy" | "degraded" | "unhealthy" | "unknown";
export type NotificationKind = "incident_detected" | "approval_requested" | "incident_resolved" | "incident_escalated" | "policy_denial";

export const INCIDENT_STATUSES: readonly IncidentStatus[] = [
  "detected",
  "triaging",
  "investigating",
  "hypothesis_formed",
  "validating",
  "remediation_planned",
  "awaiting_approval",
  "remediating",
  "verifying",
  "resolved",
  "rolled_back",
  "escalated",
  "failed",
  "closed",
];
export const SEVERITIES: readonly Severity[] = ["sev1", "sev2", "sev3", "sev4"];
export const PHASES = ["triage", "investigate", "hypothesize", "validate", "remediate", "verify", "escalate"] as const;

export const GATE_CHECKS = [
  "tool_registered",
  "tool_enabled",
  "incident_active",
  "flow_allows",
  "phase_allows",
  "category_permitted",
  "environment_allows",
  "severity_allows",
  "actor_permitted",
  "risk_within_ceiling",
  "policy_effect",
  "arguments_valid",
  "idempotency",
  "budget_available",
] as const;
export type GateCheckName = (typeof GATE_CHECKS)[number];

// ---- shared ----------------------------------------------------------------------------------

export interface Actor {
  kind: ActorKind;
  id: string;
  display_name: string | null;
  roles: Role[];
}

export interface IncidentEvent {
  id: string;
  incident_id: string;
  seq: number;
  type: string;
  at: string;
  actor: Actor;
  title: string;
  payload: Record<string, unknown>;
  trace_id: string | null;
}

export interface ExecutionBudget {
  max_iterations: number;
  max_tool_calls: number;
  max_llm_calls: number;
  max_llm_tokens: number;
  max_runtime_seconds: number;
  max_remediation_attempts: number;
}

export interface BudgetUsage {
  iterations: number;
  tool_calls: number;
  llm_calls: number;
  llm_tokens: number;
  runtime_seconds: number;
  remediation_attempts: number;
}

interface Timestamped {
  created_at: string;
  updated_at: string;
}

// ---- incidents -------------------------------------------------------------------------------

export interface AnomalySignal {
  id: string;
  service: string;
  metric: string;
  kind: SignalKind;
  observed_value: number;
  baseline_value: number;
  deviation_sigma: number;
  detector: string;
  detected_at: string;
  window_seconds: number;
  description: string;
}

export interface Incident extends Timestamped {
  id: string;
  tenant_id: string;
  environment: Environment;
  number: number;
  title: string;
  summary: string;
  severity: Severity;
  status: IncidentStatus;
  flow_name: string | null;
  flow_version: string | null;
  correlation_key: string;
  affected_services: string[];
  signals: AnomalySignal[];
  leading_hypothesis_id: string | null;
  active_action_plan_id: string | null;
  workflow_id: string | null;
  detected_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  closed_at: string | null;
  resolution_summary: string;
  root_cause_summary: string;
  remediation_attempts: number;
  version: number;
}

export interface HypothesisScore {
  evidence_strength: number;
  temporal_alignment: number;
  dependency_alignment: number;
  historical_similarity: number;
  contradiction_penalty: number;
  validation_bonus: number;
  total: number;
  explanation: string[];
}

export interface SuggestedTest {
  tool_name: string;
  arguments: Record<string, unknown>;
  expectation: string;
  would_confirm: boolean;
}

export interface HypothesisTest {
  id: string;
  hypothesis_id: string;
  tool_execution_id: string | null;
  tool_name: string;
  expectation: string;
  outcome: "confirmed" | "refuted" | "inconclusive";
  detail: string;
  at: string;
}

export interface Hypothesis extends Timestamped {
  id: string;
  incident_id: string;
  statement: string;
  category: HypothesisCategory;
  suspected_root_cause_service: string | null;
  mechanism: string;
  affected_services: string[];
  status: HypothesisStatus;
  confidence: number;
  score: HypothesisScore;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  suggested_tests: SuggestedTest[];
  tests: HypothesisTest[];
  proposed_by: "llm" | "deterministic" | "memory";
  agent_run_id: string | null;
  version: number;
}

export interface RollbackPlan {
  available: boolean;
  tool_name: string | null;
  arguments: Record<string, unknown>;
  reason: string;
}

export interface VerificationCondition {
  metric: string;
  service: string;
  comparator: "lt" | "lte" | "gt" | "gte";
  target: number | null;
  max_ratio_to_baseline: number | null;
  description: string;
}

export interface VerificationSpec {
  conditions: VerificationCondition[];
  stabilization_seconds: number;
  timeout_seconds: number;
  require_all: boolean;
}

export interface PolicyDecision {
  effect: PolicyEffect;
  matched_rule: string | null;
  reason: string;
  invariant: string | null;
  evaluated_rules: string[];
}

export interface ConditionResult {
  service: string;
  metric: string;
  ok: boolean;
  detail: string;
  value?: number;
  baseline?: number | null;
  description?: string;
}

export interface VerificationResult {
  status: VerificationStatus;
  checked_at: string;
  condition_results: ConditionResult[];
  summary: string;
  before: Record<string, number>;
  after: Record<string, number>;
}

export interface ActionPlan extends Timestamped {
  id: string;
  incident_id: string;
  hypothesis_id: string | null;
  tool_name: string;
  tool_version: string;
  arguments: Record<string, unknown>;
  reason: string;
  expected_effect: string;
  risk: RiskLevel;
  rollback: RollbackPlan;
  verification: VerificationSpec;
  timeout_seconds: number;
  status: ActionPlanStatus;
  idempotency_key: string;
  proposed_by: "llm" | "deterministic";
  agent_run_id: string | null;
  approval_id: string | null;
  policy_decision: PolicyDecision | null;
  attempt: number;
  verification_result: VerificationResult | null;
}

export interface Approval extends Timestamped {
  id: string;
  incident_id: string;
  action_plan_id: string;
  status: ApprovalStatus;
  title: string;
  summary: string;
  risk: RiskLevel;
  expected_impact: string;
  rollback_summary: string;
  hypothesis_id: string | null;
  hypothesis_statement: string;
  hypothesis_confidence: number;
  evidence_ids: string[];
  requested_by: string;
  requested_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string;
  context: Record<string, unknown>;
}

export interface AgentRun extends Timestamped {
  id: string;
  incident_id: string;
  flow_name: string;
  flow_version: string;
  phase: string;
  status: AgentRunStatus;
  budget: ExecutionBudget;
  usage: BudgetUsage;
  model: string;
  started_at: string;
  finished_at: string | null;
  termination_reason: TerminationReason | null;
  summary: string;
  error: string | null;
  steps_count: number;
  workflow_id: string | null;
  attempt: number;
}

export interface Evidence extends Timestamped {
  id: string;
  incident_id: string;
  kind: EvidenceKind;
  source: string;
  service: string | null;
  title: string;
  summary: string;
  data: Record<string, unknown>;
  strength: number;
  observed_at: string;
  tool_execution_id: string | null;
  agent_run_id: string | null;
  phase: string | null;
  tags: string[];
}

export interface EvidenceRelation {
  id: string;
  incident_id: string;
  from_id: string;
  from_kind: GraphNodeKind;
  to_id: string;
  to_kind: GraphNodeKind;
  kind: RelationKind;
  weight: number;
  created_at: string;
  created_by: string;
}

export interface EvidenceGraph {
  incident_id: string;
  evidence: Evidence[];
  relations: EvidenceRelation[];
}

export interface GateCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface AuthorizationDecision {
  request_id: string;
  tool_name: string;
  effect: PolicyEffect;
  allowed: boolean;
  checks: GateCheck[];
  denial_code: string | null;
  reason: string;
  matched_policy: string | null;
  decided_at: string;
}

export interface ToolExecution extends Timestamped {
  id: string;
  incident_id: string;
  request_id: string;
  tool_name: string;
  tool_version: string;
  category: ToolCategory;
  arguments: Record<string, unknown>;
  idempotency_key: string;
  status: ExecutionStatus;
  authorization: AuthorizationDecision;
  agent_run_id: string | null;
  action_plan_id: string | null;
  phase: string | null;
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  result: Record<string, unknown>;
  result_summary: string;
  error: string | null;
  evidence_ids: string[];
  trace_id: string | null;
}

export interface IncidentDetail {
  incident: Incident;
  hypotheses: Hypothesis[];
  action_plans: ActionPlan[];
  approvals: Approval[];
  agent_runs: AgentRun[];
  evidence: EvidenceGraph | null;
  tool_executions: ToolExecution[];
  recent_events: IncidentEvent[];
}

export interface AuditEvent {
  id: string;
  at: string;
  event_type: string;
  actor: Actor;
  incident_id: string;
  flow_name: string | null;
  phase: string | null;
  tool_name: string | null;
  action: string | null;
  decision: string | null;
  reason: string | null;
  trace_id: string | null;
  agent_run_id: string | null;
  tool_execution_id: string | null;
  data: Record<string, unknown>;
}

export interface WorkflowQuery {
  phase: string;
  status: string;
  awaiting_approval_id: string | null;
  remediation_attempts: number;
  phases: string[];
  cancelled: boolean;
}

export interface WorkflowInfo {
  workflow_id: string;
  run_id?: string;
  status: string | null;
  start_time?: string | null;
  close_time?: string | null;
  task_queue?: string;
  query?: WorkflowQuery | null;
}

// ---- agent steps -----------------------------------------------------------------------------

export interface AgentToolCall {
  tool_name: string;
  arguments_json: string;
  purpose: string;
  tests_hypothesis: string | null;
  expectation: string;
}

export interface AgentRemediation {
  tool_name: string;
  arguments_json: string;
  target_hypothesis: string;
  reason: string;
  expected_effect: string;
  rollback_tool_name: string | null;
  rollback_arguments_json: string | null;
}

export interface AgentProposal {
  observation: string;
  action: string;
  tool_call: AgentToolCall | null;
  hypotheses: Record<string, unknown>[];
  hypothesis_update: Record<string, unknown> | null;
  remediation: AgentRemediation | null;
  rationale: string;
}

export interface HypothesisPayload {
  hypothesis_id: string;
  statement: string;
  category: string;
  root_cause_service: string | null;
  status: string;
  confidence: number;
  score: HypothesisScore;
  supporting: number;
  contradicting: number;
}

export interface AgentStep {
  id: string;
  agent_run_id: string;
  incident_id: string;
  seq: number;
  phase: string;
  node: "observe" | "propose" | "execute_tool" | "apply_hypotheses" | "plan_remediation" | "conclude";
  kind: AgentStepKind;
  at: string;
  title: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  tool_execution_id: string | null;
  trace_id: string | null;
}

export interface AgentRunDetail {
  run: AgentRun;
  steps: AgentStep[];
}

// ---- approvals -------------------------------------------------------------------------------

export interface ApprovalDetail {
  approval: Approval;
  action_plan: ActionPlan;
  incident: { id: string; display_id: string; title: string; severity: Severity; status: IncidentStatus };
  hypothesis: Hypothesis | null;
  evidence: Evidence[];
}

// ---- registry --------------------------------------------------------------------------------

export interface ExitCondition {
  kind:
    | "min_evidence"
    | "min_hypotheses"
    | "min_hypothesis_confidence"
    | "hypothesis_validated"
    | "action_planned"
    | "no_action_required"
    | "min_iterations";
  value: number | null;
  description: string;
}

export interface PhaseTransition {
  on: "exit_conditions_met" | "exhausted" | "escalate" | "no_action_required";
  to: string;
}

export interface FlowPhase {
  name: string;
  objective: string;
  allowed_tools: string[];
  max_iterations: number;
  timeout_seconds: number;
  risk_ceiling: RiskLevel;
  exit_conditions: ExitCondition[];
  transitions: PhaseTransition[];
  terminal: boolean;
  guidance: string;
  plans_remediation: boolean;
}

export interface FlowPack {
  name: string;
  version: string;
  description: string;
  applies_to: SignalKind[];
  priority: number;
  initial_phase: string;
  phases: FlowPhase[];
  remediation_tools: string[];
  remediation_risk_ceiling: RiskLevel;
  budget: ExecutionBudget;
  severity_budget_factor: Record<Severity, number>;
  checksum: string;
}

export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  default?: unknown;
  enum?: unknown[];
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  [key: string]: unknown;
}

export interface ToolSpec {
  name: string;
  version: string;
  description: string;
  category: ToolCategory;
  risk: RiskLevel;
  idempotent: boolean;
  timeout_seconds: number;
  retry: { max_attempts: number; initial_backoff_seconds: number; backoff_multiplier: number; max_backoff_seconds: number };
  capabilities: string[];
  allowed_environments: Environment[];
  min_severity: Severity | null;
  enabled: boolean;
  arguments_schema: JsonSchema;
  produces_evidence: boolean;
  verification_metrics: string[];
}

export interface ToolDetail extends ToolSpec {
  used_in: Record<string, string[]>;
}

export interface PolicyRule {
  name: string;
  description: string;
  priority: number;
  effect: PolicyEffect;
  tools: string[];
  categories: ToolCategory[];
  min_risk: RiskLevel | null;
  max_risk: RiskLevel | null;
  environments: Environment[];
  severities: Severity[];
  phases: string[];
  flows: string[];
  actor_kinds: ActorKind[];
  required_role: Role | null;
  in_agent_loop: boolean | null;
  reason: string;
}

export interface PolicySet {
  rules: PolicyRule[];
  invariants: { name: string; description: string }[];
  default: string;
  environment: Environment;
}

// ---- memory ----------------------------------------------------------------------------------

export interface IncidentMemory extends Timestamped {
  id: string;
  incident_id: string;
  incident_number: number;
  title: string;
  severity: Severity;
  symptoms: string[];
  affected_services: string[];
  root_cause: string;
  root_cause_service: string | null;
  root_cause_category: string;
  evidence_summary: string[];
  actions: Record<string, unknown>[];
  resolution: string;
  verification_summary: string;
  duration_seconds: number;
  outcome: "resolved" | "escalated" | "false_positive";
  lessons: string[];
  embedding_text: string;
  embedding_model: string | null;
  resolved_at: string | null;
}

export interface MemoryHit {
  similarity: number;
  matched_on: string;
  memory: IncidentMemory;
}

// ---- simulation ------------------------------------------------------------------------------

export interface Scenario {
  id: string;
  title: string;
  description: string;
  fault_type: string;
  default_params: Record<string, unknown>;
  root_cause_service: string | null;
  root_cause_category: string;
  expected_symptoms: string[];
  correct_remediations: { action: string; target: string; extra: Record<string, unknown> }[];
  incorrect_remediations: { action: string; target: string; extra: Record<string, unknown> }[];
  expect_no_action: boolean;
  expect_escalation: boolean;
  self_resolving_seconds: number | null;
  detection_hint_metrics: string[];
}

export interface Fault {
  id: string;
  scenario_id: string;
  fault_type: string;
  params: Record<string, unknown>;
  started_at: number;
  duration_seconds: number | null;
  cleared_at: number | null;
  cleared_by: string | null;
  active: boolean;
}

export interface ServiceState {
  version: string;
  replicas: number;
  up: boolean;
  error_rate: number;
  latency_p95_ms: number;
  health: HealthState;
}

export interface InfraState {
  connections: number;
  saturation: number;
  up: boolean;
}

export type ComponentKind = "gateway" | "service" | "database" | "cache";

export interface TopologyNode {
  name: string;
  kind: ComponentKind;
  tier: number;
  owner: string;
  replicas: number;
  version: string;
  state: ServiceState | InfraState;
}

export interface TopologyEdge {
  source: string;
  target: string;
  protocol: string;
  critical: boolean;
}

export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  time: string;
  active_faults: Fault[];
}

export interface SimulationState {
  time: string;
  tick_count: number;
  seed: number;
  active_faults: Fault[];
  services: Record<string, ServiceState>;
  infra: Record<string, InfraState>;
}

export interface SimulationAction {
  at: number;
  kind: string;
  scenario?: string;
  fault_id?: string;
  params?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface MetricSample {
  at: string;
  value: number;
}

export interface MetricSeries {
  component: string;
  metric: string;
  samples: MetricSample[];
}

export interface Baseline {
  component: string;
  metric: string;
  baseline: number | null;
}

export interface ComponentCurrent {
  component: string;
  time: string;
  metrics: Record<string, number>;
}

// ---- system ----------------------------------------------------------------------------------

export interface Health {
  status: string;
  version: string;
}

export interface ReadyCheck {
  ok: boolean;
  [key: string]: unknown;
}

export interface Readiness {
  status: "ready" | "not_ready";
  checks: Record<string, ReadyCheck>;
}

export interface SystemInfo {
  version: string;
  environment: Environment;
  llm: { provider: string; reasoner: string; fast: string; healthy: boolean };
  telemetry_provider: string;
  temporal: boolean;
  flows: string[];
  tools: number;
  policy_rules: number;
}

export interface Notification {
  id: string;
  kind: NotificationKind;
  incident_id: string | null;
  title: string;
  body: string;
  at: string;
  read: boolean;
  data: Record<string, unknown>;
}

export function isServiceState(state: ServiceState | InfraState): state is ServiceState {
  return "health" in state;
}

export function displayId(incident: { number: number }): string {
  return `INC-${incident.number}`;
}

export function flowRef(flowName: string | null, flowVersion: string | null): string | null {
  if (!flowName) return null;
  return flowVersion ? `${flowName}@${flowVersion}` : flowName;
}
