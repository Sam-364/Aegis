"use client";

import { isApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";

/** Visible, specific treatment of `application/problem+json` errors. Never a generic toast. */
export function ProblemBanner({ error, onRetry, compact = false, className = "" }: { error: unknown; onRetry?: () => void; compact?: boolean; className?: string }) {
  if (!error) return null;
  const api = isApiError(error) ? error : null;
  const status = api?.status ?? null;
  const tone = status === 0 || (status != null && status >= 500) ? "#ff4d4f" : status === 409 || status === 422 ? "#ff8a3d" : status === 403 || status === 401 ? "#e6c229" : "#8a9099";
  const title = api?.title ?? (error instanceof Error ? error.message : "Unexpected error");
  const label = api ? (api.isNetwork ? "network" : `HTTP ${api.status}${api.type ? ` · ${api.type}` : ""}`) : "client error";

  return (
    <div className={`rounded-sm border bg-panel ${compact ? "px-3 py-2" : "px-4 py-3"} ${className}`} style={{ borderColor: `${tone}66`, borderLeftWidth: 3, borderLeftColor: tone }} role="alert">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="micro-mono" style={{ color: tone }}>
            {label}
          </div>
          <div className="mt-0.5 text-[12.5px] font-medium text-ink">{title}</div>
          {api?.detail ? <div className="mt-0.5 text-[11.5px] leading-snug text-muted break-words">{api.detail}</div> : null}
          {api?.errors.length ? (
            <ul className="mono mt-1 space-y-0.5 text-[11px] text-muted">
              {api.errors.map((e, i) => (
                <li key={i}>
                  <span className="text-sev2">{e.loc?.join(".")}</span> {e.msg}
                </li>
              ))}
            </ul>
          ) : null}
          {api?.retryAfter ? <div className="mono mt-1 text-[11px] text-muted">retry after {api.retryAfter}s</div> : null}
          {!compact && api ? (
            <div className="mono mt-1.5 flex flex-wrap gap-x-3 text-[10.5px] text-faint">
              <span>
                {api.method} {api.url.replace(/^https?:\/\/[^/]+/, "")}
              </span>
              {api.requestId ? <span>request_id {api.requestId}</span> : null}
            </div>
          ) : null}
        </div>
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  );
}
