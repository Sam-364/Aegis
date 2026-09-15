"use client";

import { useEffect, useState } from "react";
import { config } from "@/lib/config";
import { demoCapturedAt } from "@/lib/demo";
import { TONE_COLOR } from "@/lib/colors";

/**
 * Says plainly that this console is a recording.
 *
 * A console that looks live but cannot act is worse than no console: the whole point of Aegis is
 * that what you see is what happened. So the banner is always present in demo mode, states when
 * the data was captured, and points at the way to run the real thing.
 */
export function DemoBanner() {
  const [capturedAt, setCapturedAt] = useState<string>("");

  useEffect(() => {
    if (!config.demo) return;
    void demoCapturedAt().then(setCapturedAt);
  }, []);

  if (!config.demo) return null;

  const when = capturedAt ? new Date(capturedAt) : null;
  const stamp =
    when && !Number.isNaN(when.getTime())
      ? when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
      : null;

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b px-4 py-1.5 text-[11px]"
      style={{
        borderColor: `${TONE_COLOR.warn}33`,
        background: `${TONE_COLOR.warn}12`,
        color: TONE_COLOR.warn,
      }}
      role="status"
    >
      <span className="font-mono font-semibold uppercase tracking-wider">Recording</span>
      <span className="text-muted">
        Real incidents captured from a running stack{stamp ? ` on ${stamp}` : ""}. Nothing here is
        live, and approvals, rejections and fault injection are disabled.
      </span>
      <a
        className="ml-auto underline decoration-dotted underline-offset-2 hover:opacity-80"
        href="https://github.com/Sam-364/Aegis#quick-start"
        target="_blank"
        rel="noreferrer"
      >
        Run it for real →
      </a>
    </div>
  );
}
