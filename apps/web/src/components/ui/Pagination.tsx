"use client";

import { Button } from "@/components/ui/Button";

export function Pagination({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(total, offset + limit);
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-2">
      <span className="mono text-[11px] text-muted">
        {from}–{to} of {total}
      </span>
      <div className="flex gap-1">
        <Button size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          ← Prev
        </Button>
        <Button size="sm" disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Next →
        </Button>
      </div>
    </div>
  );
}
