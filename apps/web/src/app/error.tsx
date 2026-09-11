"use client";

import { ProblemBanner } from "@/components/ui/Problem";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="p-6">
      <ProblemBanner error={error} onRetry={reset} />
    </div>
  );
}
