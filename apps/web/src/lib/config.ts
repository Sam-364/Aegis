/** Runtime configuration read from NEXT_PUBLIC_* environment variables (inlined at build time). */

function trimSlash(s: string): string {
  return s.replace(/\/+$/, "");
}

export const config = {
  apiUrl: trimSlash(process.env.NEXT_PUBLIC_AEGIS_API_URL ?? "http://localhost:8600"),
  apiKey: process.env.NEXT_PUBLIC_AEGIS_API_KEY ?? "",
  temporalUiUrl: trimSlash(process.env.NEXT_PUBLIC_TEMPORAL_UI_URL ?? "http://localhost:8233"),
  grafanaUrl: trimSlash(process.env.NEXT_PUBLIC_GRAFANA_URL ?? "http://localhost:3000"),
  prometheusUrl: trimSlash(process.env.NEXT_PUBLIC_PROMETHEUS_URL ?? "http://localhost:9090"),
  temporalNamespace: process.env.NEXT_PUBLIC_TEMPORAL_NAMESPACE ?? "default",
} as const;

export function temporalWorkflowUrl(workflowId: string): string {
  return `${config.temporalUiUrl}/namespaces/${encodeURIComponent(config.temporalNamespace)}/workflows/${encodeURIComponent(workflowId)}`;
}

export function apiDocsUrl(): string {
  return `${config.apiUrl}/api/docs`;
}
