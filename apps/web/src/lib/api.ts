/**
 * Typed fetch client for the Aegis control-plane API.
 *
 * - Base URL from NEXT_PUBLIC_AEGIS_API_URL, optional key sent as `X-Aegis-Key`.
 * - Errors surface as `ApiError` carrying the `application/problem+json` body
 *   ({type,title,status,detail,request_id}). `detail` may be a string, `{}` or `{errors:[{loc,msg}]}`.
 */
import { ApiError, type Problem, type ProblemError } from "@/lib/api-error";
import { config } from "@/lib/config";
import { demoGet, demoWrite } from "@/lib/demo";
import type {
  AgentRun,
  AgentRunDetail,
  AgentStep,
  Approval,
  ApprovalDetail,
  AuditEvent,
  Baseline,
  ComponentCurrent,
  EvidenceGraph,
  Fault,
  FlowPack,
  Health,
  IncidentDetail,
  IncidentEvent,
  IncidentMemory,
  IncidentStatus,
  IncidentSummary,
  MemoryHit,
  MetricSeries,
  Notification,
  Page,
  PolicySet,
  Principal,
  Readiness,
  Scenario,
  Severity,
  SimulationAction,
  SimulationState,
  StatsResponse,
  SystemInfo,
  ToolDetail,
  ToolSpec,
  Topology,
  WorkflowInfo,
} from "@/lib/types";

export { ApiError } from "@/lib/api-error";
export type { Problem, ProblemError } from "@/lib/api-error";

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export type QueryValue = string | number | boolean | null | undefined | ReadonlyArray<string | number | boolean>;
export type Query = Record<string, QueryValue>;

/** Build an absolute API URL. Arrays are encoded as repeated keys (FastAPI `list[...]` style). */
export function apiUrl(path: string, query?: Query): string {
  const url = new URL(path.startsWith("/") ? path : `/${path}`, `${config.apiUrl}/`);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) {
        for (const item of v) url.searchParams.append(k, String(item));
      } else {
        url.searchParams.set(k, String(v));
      }
    }
  }
  return url.toString();
}

/** URL for an EventSource; the key travels as `?key=` because EventSource cannot set headers. */
export function streamUrl(path: string, query?: Query): string {
  return apiUrl(path, { ...(query ?? {}), ...(config.apiKey ? { key: config.apiKey } : {}) });
}

export function authHeaders(): Record<string, string> {
  return config.apiKey ? { "X-Aegis-Key": config.apiKey } : {};
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

function titleForStatus(status: number): string {
  switch (status) {
    case 400:
      return "Bad request";
    case 401:
      return "Unauthorized";
    case 403:
      return "Forbidden";
    case 404:
      return "Not found";
    case 409:
      return "Conflict";
    case 422:
      return "Validation failed";
    case 429:
      return "Rate limited";
    case 500:
      return "Internal server error";
    case 502:
      return "Bad gateway";
    case 503:
      return "Service unavailable";
    default:
      return `HTTP ${status}`;
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function extractErrors(detail: unknown): ProblemError[] {
  const list = Array.isArray(detail) ? detail : isRecord(detail) && Array.isArray(detail.errors) ? detail.errors : [];
  return list.filter(isRecord).map((d) => ({
    loc: Array.isArray(d.loc) ? (d.loc as (string | number)[]) : undefined,
    msg: typeof d.msg === "string" ? d.msg : undefined,
    type: typeof d.type === "string" ? d.type : undefined,
  }));
}

function detailToString(detail: unknown, errors: ProblemError[]): string | undefined {
  if (typeof detail === "string") return detail || undefined;
  if (errors.length > 0) {
    return errors
      .map((e) => (e.loc && e.loc.length > 0 ? `${e.loc.join(".")}: ${e.msg ?? ""}` : (e.msg ?? "")))
      .join("; ");
  }
  if (isRecord(detail) && Object.keys(detail).length > 0) return JSON.stringify(detail);
  return undefined;
}

async function readProblem(res: Response): Promise<{ problem: Problem | undefined; text: string }> {
  const text = await res.text().catch(() => "");
  if (!text) return { problem: undefined, text };
  try {
    const body: unknown = JSON.parse(text);
    return { problem: isRecord(body) ? (body as Problem) : undefined, text };
  } catch {
    return { problem: undefined, text };
  }
}

export async function apiFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  if (config.demo) {
    if (method !== "GET") demoWrite(path, method);
    return demoGet<T>(path, opts.query as Record<string, unknown> | undefined);
  }
  const url = apiUrl(path, opts.query);
  const headers: Record<string, string> = {
    Accept: "application/json, application/problem+json",
    ...authHeaders(),
  };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError({
      status: 0,
      title: "API unreachable",
      detail: `Could not reach ${config.apiUrl}. ${e instanceof Error ? e.message : ""}`.trim(),
      type: "network_error",
      url,
      method,
    });
  }

  if (!res.ok) {
    const { problem, text } = await readProblem(res);
    const errors = extractErrors(problem?.detail);
    const retryAfterRaw = res.headers.get("retry-after");
    const retryAfter = retryAfterRaw ? Number(retryAfterRaw) : undefined;
    throw new ApiError({
      status: res.status,
      title: (typeof problem?.title === "string" && problem.title) || titleForStatus(res.status),
      detail: problem ? detailToString(problem.detail, errors) : text.slice(0, 300) || undefined,
      type: typeof problem?.type === "string" ? problem.type : undefined,
      requestId: (typeof problem?.request_id === "string" ? problem.request_id : undefined) ?? res.headers.get("x-request-id") ?? undefined,
      errors,
      problem,
      url,
      method,
      retryAfter: Number.isFinite(retryAfter) ? retryAfter : undefined,
    });
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError({ status: res.status, title: "Malformed response", detail: text.slice(0, 200), url, method });
  }
}

export const http = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => apiFetch<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) => apiFetch<T>(path, { method: "POST", body, query }),
  del: <T>(path: string, query?: Query) => apiFetch<T>(path, { method: "DELETE", query }),
};

// ---- typed endpoints -------------------------------------------------------------------------

export interface IncidentListParams {
  status?: IncidentStatus[];
  severity?: Severity[];
  active?: boolean;
  limit?: number;
  offset?: number;
}

export function isoWindow(minutes: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - minutes * 60_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

export const api = {
  system: {
    health: () => http.get<Health>("/health"),
    ready: () => http.get<Readiness>("/ready"),
    me: () => http.get<Principal>("/api/v1/me"),
    info: () => http.get<SystemInfo>("/api/v1/system/info"),
    notifications: (unreadOnly = false, limit = 50) =>
      http.get<Notification[]>("/api/v1/notifications", { unread_only: unreadOnly, limit }),
    markRead: (id: string) => http.post<void>(`/api/v1/notifications/${id}/read`),
  },
  incidents: {
    list: (p: IncidentListParams = {}) =>
      http.get<Page<IncidentSummary>>("/api/v1/incidents", {
        status: p.status,
        severity: p.severity,
        active: p.active,
        limit: p.limit,
        offset: p.offset,
      }),
    stats: () => http.get<StatsResponse>("/api/v1/incidents/stats"),
    get: (id: string) => http.get<IncidentDetail>(`/api/v1/incidents/${id}`),
    timeline: (id: string, afterSeq = 0, limit = 500) =>
      http.get<IncidentEvent[]>(`/api/v1/incidents/${id}/timeline`, { after_seq: afterSeq, limit }),
    evidence: (id: string) => http.get<EvidenceGraph>(`/api/v1/incidents/${id}/evidence`),
    audit: (id: string, limit = 200, offset = 0) => http.get<AuditEvent[]>(`/api/v1/incidents/${id}/audit`, { limit, offset }),
    approvals: (id: string) => http.get<Approval[]>(`/api/v1/incidents/${id}/approvals`),
    agentRuns: (id: string) => http.get<AgentRun[]>(`/api/v1/incidents/${id}/agent-runs`),
    workflow: (id: string) => http.get<WorkflowInfo>(`/api/v1/incidents/${id}/workflow`),
    acknowledge: (id: string) => http.post<IncidentSummary>(`/api/v1/incidents/${id}/acknowledge`),
    close: (id: string, reason: string) => http.post<IncidentSummary>(`/api/v1/incidents/${id}/close`, { reason }),
    resolve: (id: string, reason: string) => http.post<IncidentSummary>(`/api/v1/incidents/${id}/resolve`, { reason }),
    reopen: (id: string, reason: string) => http.post<IncidentSummary>(`/api/v1/incidents/${id}/reopen`, { reason }),
  },
  approvals: {
    list: (status = "pending", limit = 100) => http.get<Approval[]>("/api/v1/approvals", { status, limit }),
    get: (id: string) => http.get<ApprovalDetail>(`/api/v1/approvals/${id}`),
    approve: (id: string, reason: string) => http.post<Approval>(`/api/v1/approvals/${id}/approve`, { reason }),
    reject: (id: string, reason: string) => http.post<Approval>(`/api/v1/approvals/${id}/reject`, { reason }),
  },
  agentRuns: {
    list: (limit = 50) => http.get<AgentRun[]>("/api/v1/agent-runs", { limit }),
    get: (id: string) => http.get<AgentRunDetail>(`/api/v1/agent-runs/${id}`),
    steps: (id: string) => http.get<AgentStep[]>(`/api/v1/agent-runs/${id}/steps`),
  },
  registry: {
    flows: () => http.get<FlowPack[]>("/api/v1/flows"),
    flow: (name: string, version?: string) => http.get<FlowPack>(`/api/v1/flows/${name}`, { version }),
    tools: () => http.get<ToolSpec[]>("/api/v1/tools"),
    tool: (name: string) => http.get<ToolDetail>(`/api/v1/tools/${name}`),
    policies: () => http.get<PolicySet>("/api/v1/policies"),
  },
  memory: {
    list: (limit = 50, offset = 0) => http.get<Page<IncidentMemory>>("/api/v1/memory", { limit, offset }),
    search: (q: string, services: string[] = [], limit = 10) =>
      http.get<MemoryHit[]>("/api/v1/memory/search", { q, services: services.join(","), limit }),
  },
  simulation: {
    scenarios: () => http.get<Scenario[]>("/api/v1/simulation/scenarios"),
    faults: (activeOnly = false) => http.get<Fault[]>("/api/v1/simulation/faults", { active_only: activeOnly }),
    inject: (scenarioId: string, params?: Record<string, unknown>) =>
      http.post<Fault>("/api/v1/simulation/faults", params ? { scenario_id: scenarioId, params } : { scenario_id: scenarioId }),
    clear: (faultId: string) => http.del<unknown>(`/api/v1/simulation/faults/${faultId}`),
    topology: () => http.get<Topology>("/api/v1/simulation/topology"),
    state: () => http.get<SimulationState>("/api/v1/simulation/state"),
    actions: () => http.get<SimulationAction[]>("/api/v1/simulation/actions"),
    metrics: (component: string, metric: string, start: string, end: string) =>
      http.get<MetricSeries>(`/api/v1/simulation/metrics/${component}`, { metric, start, end }),
    baseline: (component: string, metric: string) =>
      http.get<Baseline>(`/api/v1/simulation/components/${component}/baseline/${metric}`),
    current: (component: string) => http.get<ComponentCurrent>(`/api/v1/simulation/components/${component}/current`),
    advance: (seconds: number) => http.post<SimulationState>("/api/v1/simulation/control/advance", { seconds }),
    reset: (seed: number) => http.post<SimulationState>("/api/v1/simulation/control/reset", { seed }),
  },
};
