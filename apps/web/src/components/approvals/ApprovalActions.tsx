"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ProblemBanner } from "@/components/ui/Problem";
import { useApprovalDecision } from "@/lib/hooks";

/**
 * Inline approve / reject with a two-step confirm and a reason field.
 * `weighty` renders the large console variant used on the incident page.
 */
export function ApprovalActions({ approvalId, weighty = false, onDecided }: { approvalId: string; weighty?: boolean; onDecided?: () => void }) {
  const [reason, setReason] = useState("");
  const [arm, setArm] = useState<"approve" | "reject" | null>(null);
  const decide = useApprovalDecision();

  useEffect(() => {
    if (!arm) return;
    const t = setTimeout(() => setArm(null), 6000);
    return () => clearTimeout(t);
  }, [arm]);

  const fire = (decision: "approve" | "reject") => {
    if (arm !== decision) {
      setArm(decision);
      return;
    }
    decide.mutate({ id: approvalId, decision, reason: reason.trim() }, { onSuccess: () => onDecided?.() });
    setArm(null);
  };

  return (
    <div className={weighty ? "space-y-3" : "space-y-2"}>
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value.slice(0, 500))}
        placeholder={weighty ? "Decision reason (recorded in the audit trail)" : "Reason (optional)"}
        rows={weighty ? 2 : 1}
        className="field w-full resize-none font-sans"
      />
      <div className={`flex gap-2 ${weighty ? "" : "justify-end"}`}>
        <Button variant={arm === "approve" ? "primary" : "primary"} size={weighty ? "lg" : "sm"} loading={decide.isPending && decide.variables?.decision === "approve"} onClick={() => fire("approve")} className={weighty ? "flex-1 justify-center" : ""} disabled={decide.isPending}>
          {arm === "approve" ? "Confirm — execute once" : weighty ? "Approve and execute" : "Approve"}
        </Button>
        <Button variant={arm === "reject" ? "danger-solid" : "danger"} size={weighty ? "lg" : "sm"} loading={decide.isPending && decide.variables?.decision === "reject"} onClick={() => fire("reject")} className={weighty ? "flex-1 justify-center" : ""} disabled={decide.isPending}>
          {arm === "reject" ? "Confirm rejection" : "Reject"}
        </Button>
      </div>
      {arm ? <div className="micro text-amber">Click again within 6s to confirm. The workflow will {arm === "approve" ? "execute the remediation exactly once" : "abandon this plan"}.</div> : null}
      {decide.error ? <ProblemBanner error={decide.error} compact /> : null}
    </div>
  );
}
