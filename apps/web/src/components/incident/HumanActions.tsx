"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { ProblemBanner } from "@/components/ui/Problem";
import { isActiveStatus } from "@/lib/colors";
import { useIncidentAction, type IncidentAction } from "@/lib/hooks";
import type { Incident } from "@/lib/types";

const LABEL: Record<IncidentAction, string> = { acknowledge: "Acknowledge", close: "Close", resolve: "Resolve", reopen: "Reopen" };

export function HumanActions({ incident }: { incident: Incident }) {
  const act = useIncidentAction(incident.id);
  const [pending, setPending] = useState<IncidentAction | null>(null);
  const [reason, setReason] = useState("");
  const active = isActiveStatus(incident.status);

  const available: IncidentAction[] = [];
  if (active && !incident.acknowledged_at) available.push("acknowledge");
  if (incident.status === "escalated") available.push("resolve");
  if (active || incident.status === "escalated" || incident.status === "resolved" || incident.status === "failed" || incident.status === "rolled_back") available.push("close");
  if (incident.status === "resolved" || incident.status === "closed") available.push("reopen");

  const run = (action: IncidentAction) => {
    if (action === "acknowledge") {
      act.mutate({ action });
      return;
    }
    if (pending !== action) {
      setPending(action);
      setReason("");
      return;
    }
    act.mutate({ action, reason: reason.trim() }, { onSuccess: () => setPending(null) });
  };

  if (available.length === 0) return null;
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-1.5">
        {available.map((a) => (
          <Button key={a} size="sm" variant={a === "close" ? "danger" : a === "acknowledge" ? "primary" : "outline"} onClick={() => run(a)} loading={act.isPending && act.variables?.action === a}>
            {pending === a ? `Confirm ${LABEL[a].toLowerCase()}` : LABEL[a]}
          </Button>
        ))}
        {pending ? (
          <Button size="sm" variant="ghost" onClick={() => setPending(null)}>
            cancel
          </Button>
        ) : null}
      </div>
      {pending ? <input value={reason} onChange={(e) => setReason(e.target.value.slice(0, 500))} placeholder={`Reason to ${pending} (recorded in audit)`} className="field w-[360px]" autoFocus /> : null}
      {act.error ? <ProblemBanner error={act.error} compact className="w-[360px]" /> : null}
    </div>
  );
}
