"use client";

/** React Query hooks, one per resource. Query keys are namespaced so SSE invalidation is cheap. */
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { api, isoWindow, type IncidentListParams } from "@/lib/api";
import type { IncidentDetail } from "@/lib/types";

export const qk = {
  health: ["health"] as const,
  ready: ["ready"] as const,
  me: ["me"] as const,
  info: ["system", "info"] as const,
  notifications: ["notifications"] as const,
  incidents: (p: IncidentListParams) => ["incidents", "list", p] as const,
  incidentsAll: ["incidents"] as const,
  stats: ["incidents", "stats"] as const,
  incident: (id: string) => ["incidents", "detail", id] as const,
  timeline: (id: string) => ["incidents", "timeline", id] as const,
  audit: (id: string) => ["incidents", "audit", id] as const,
  workflow: (id: string) => ["incidents", "workflow", id] as const,
  incidentApprovals: (id: string) => ["incidents", "approvals", id] as const,
  approvals: (status: string) => ["approvals", "list", status] as const,
  approvalsAll: ["approvals"] as const,
  approval: (id: string) => ["approvals", "detail", id] as const,
  agentRuns: ["agent-runs", "list"] as const,
  agentRun: (id: string) => ["agent-runs", "detail", id] as const,
  agentRunSteps: (id: string) => ["agent-runs", "steps", id] as const,
  flows: ["registry", "flows"] as const,
  tools: ["registry", "tools"] as const,
  tool: (name: string) => ["registry", "tool", name] as const,
  policies: ["registry", "policies"] as const,
  memories: (limit: number, offset: number) => ["memory", "list", limit, offset] as const,
  memorySearch: (q: string, services: string[]) => ["memory", "search", q, services] as const,
  scenarios: ["simulation", "scenarios"] as const,
  faults: (activeOnly: boolean) => ["simulation", "faults", activeOnly] as const,
  faultsAll: ["simulation", "faults"] as const,
  topology: ["simulation", "topology"] as const,
  simState: ["simulation", "state"] as const,
  simActions: ["simulation", "actions"] as const,
  metrics: (component: string, metric: string, minutes: number) => ["simulation", "metrics", component, metric, minutes] as const,
  baseline: (component: string, metric: string) => ["simulation", "baseline", component, metric] as const,
};

// ---- system -----------------------------------------------------------------------------------

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: api.system.health, refetchInterval: 30_000 });
}
export function useReady() {
  return useQuery({ queryKey: qk.ready, queryFn: api.system.ready, refetchInterval: 15_000, retry: false });
}
export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: api.system.me, staleTime: 5 * 60_000 });
}
export function useSystemInfo() {
  return useQuery({ queryKey: qk.info, queryFn: api.system.info, staleTime: 60_000 });
}
export function useNotifications(limit = 50) {
  return useQuery({ queryKey: [...qk.notifications, limit], queryFn: () => api.system.notifications(false, limit), refetchInterval: 30_000 });
}

// ---- incidents -------------------------------------------------------------------------------

export function useIncidents(p: IncidentListParams, opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: qk.incidents(p),
    queryFn: () => api.incidents.list(p),
    placeholderData: keepPreviousData,
    refetchInterval: opts.refetchInterval ?? 15_000,
  });
}
export function useIncidentStats() {
  return useQuery({ queryKey: qk.stats, queryFn: api.incidents.stats, refetchInterval: 15_000 });
}
export function useIncident(id: string, opts: { enabled?: boolean } = {}) {
  return useQuery<IncidentDetail>({
    queryKey: qk.incident(id),
    queryFn: () => api.incidents.get(id),
    enabled: opts.enabled ?? true,
    refetchInterval: 30_000,
  });
}
export function useTimeline(id: string) {
  return useQuery({ queryKey: qk.timeline(id), queryFn: () => api.incidents.timeline(id, 0, 500), staleTime: Infinity });
}
export function useAudit(id: string, enabled = true) {
  return useQuery({ queryKey: qk.audit(id), queryFn: () => api.incidents.audit(id, 200, 0), enabled });
}
export function useWorkflow(id: string, enabled = true) {
  return useQuery({ queryKey: qk.workflow(id), queryFn: () => api.incidents.workflow(id), enabled, refetchInterval: 20_000, retry: false });
}
export function useIncidentApprovals(id: string, enabled = true) {
  return useQuery({ queryKey: qk.incidentApprovals(id), queryFn: () => api.incidents.approvals(id), enabled });
}

export type IncidentAction = "acknowledge" | "close" | "resolve" | "reopen";

export function useIncidentAction(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ action, reason }: { action: IncidentAction; reason?: string }) => {
      switch (action) {
        case "acknowledge":
          return api.incidents.acknowledge(id);
        case "close":
          return api.incidents.close(id, reason ?? "");
        case "resolve":
          return api.incidents.resolve(id, reason ?? "");
        case "reopen":
          return api.incidents.reopen(id, reason ?? "");
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.incident(id) });
      void qc.invalidateQueries({ queryKey: qk.incidentsAll });
    },
  });
}

// ---- approvals -------------------------------------------------------------------------------

export function useApprovals(status = "pending", opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: qk.approvals(status),
    queryFn: () => api.approvals.list(status, 100),
    refetchInterval: opts.refetchInterval ?? 10_000,
  });
}
export function useApproval(id: string | null) {
  return useQuery({ queryKey: qk.approval(id ?? ""), queryFn: () => api.approvals.get(id ?? ""), enabled: !!id });
}
export function useApprovalDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision, reason }: { id: string; decision: "approve" | "reject"; reason: string }) =>
      decision === "approve" ? api.approvals.approve(id, reason) : api.approvals.reject(id, reason),
    onSuccess: (approval) => {
      void qc.invalidateQueries({ queryKey: qk.approvalsAll });
      void qc.invalidateQueries({ queryKey: qk.incident(approval.incident_id) });
      void qc.invalidateQueries({ queryKey: qk.incidentsAll });
    },
  });
}

// ---- agent runs ------------------------------------------------------------------------------

export function useAgentRuns(limit = 50) {
  return useQuery({ queryKey: [...qk.agentRuns, limit], queryFn: () => api.agentRuns.list(limit), refetchInterval: 15_000 });
}
export function useAgentRun(id: string) {
  return useQuery({ queryKey: qk.agentRun(id), queryFn: () => api.agentRuns.get(id) });
}
export function useAgentRunSteps(id: string, enabled: boolean, live = false) {
  return useQuery({
    queryKey: qk.agentRunSteps(id),
    queryFn: () => api.agentRuns.steps(id),
    enabled,
    refetchInterval: live ? 5_000 : false,
  });
}

// ---- registry --------------------------------------------------------------------------------

export function useFlows() {
  return useQuery({ queryKey: qk.flows, queryFn: api.registry.flows, staleTime: 5 * 60_000 });
}
export function useTools() {
  return useQuery({ queryKey: qk.tools, queryFn: api.registry.tools, staleTime: 5 * 60_000 });
}
export function useTool(name: string | null) {
  return useQuery({ queryKey: qk.tool(name ?? ""), queryFn: () => api.registry.tool(name ?? ""), enabled: !!name, staleTime: 5 * 60_000 });
}
export function usePolicies() {
  return useQuery({ queryKey: qk.policies, queryFn: api.registry.policies, staleTime: 5 * 60_000 });
}

// ---- memory ----------------------------------------------------------------------------------

export function useMemories(limit = 50, offset = 0) {
  return useQuery({ queryKey: qk.memories(limit, offset), queryFn: () => api.memory.list(limit, offset), placeholderData: keepPreviousData });
}
export function useMemorySearch(q: string, services: string[]) {
  const enabled = q.trim().length >= 2;
  return useQuery({
    queryKey: qk.memorySearch(q.trim(), services),
    queryFn: () => api.memory.search(q.trim(), services, 10),
    enabled,
    placeholderData: keepPreviousData,
  });
}

// ---- simulation ------------------------------------------------------------------------------

export function useScenarios() {
  return useQuery({ queryKey: qk.scenarios, queryFn: api.simulation.scenarios, staleTime: 5 * 60_000 });
}
export function useFaults(activeOnly = false, refetchInterval: number | false = 5_000) {
  return useQuery({ queryKey: qk.faults(activeOnly), queryFn: () => api.simulation.faults(activeOnly), refetchInterval });
}
export function useTopology(refetchInterval: number | false = 5_000) {
  return useQuery({ queryKey: qk.topology, queryFn: api.simulation.topology, refetchInterval });
}
export function useSimulationState(refetchInterval: number | false = 5_000) {
  return useQuery({ queryKey: qk.simState, queryFn: api.simulation.state, refetchInterval });
}
export function useSimulationActions() {
  return useQuery({ queryKey: qk.simActions, queryFn: api.simulation.actions, refetchInterval: 10_000 });
}
export function useMetricSeries(component: string, metric: string, minutes = 15, refetchInterval: number | false = 10_000) {
  return useQuery({
    queryKey: qk.metrics(component, metric, minutes),
    queryFn: () => {
      const { start, end } = isoWindow(minutes);
      return api.simulation.metrics(component, metric, start, end);
    },
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}
export function useBaseline(component: string, metric: string) {
  return useQuery({ queryKey: qk.baseline(component, metric), queryFn: () => api.simulation.baseline(component, metric), staleTime: 60_000 });
}

export function useSimulationMutations() {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["simulation"] });
  };
  const inject = useMutation({
    mutationFn: ({ scenarioId, params }: { scenarioId: string; params?: Record<string, unknown> }) => api.simulation.inject(scenarioId, params),
    onSuccess: invalidate,
  });
  const clear = useMutation({ mutationFn: (faultId: string) => api.simulation.clear(faultId), onSuccess: invalidate });
  const advance = useMutation({ mutationFn: (seconds: number) => api.simulation.advance(seconds), onSuccess: invalidate });
  const reset = useMutation({ mutationFn: (seed: number) => api.simulation.reset(seed), onSuccess: invalidate });
  return { inject, clear, advance, reset };
}
