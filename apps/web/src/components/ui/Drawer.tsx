"use client";

import { useEffect, type ReactNode } from "react";

export function Drawer({ open, onClose, title, kicker, children, width = 560, footer }: { open: boolean; onClose: () => void; title: ReactNode; kicker?: ReactNode; children: ReactNode; width?: number; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-canvas/70 animate-fade-in" />
      <aside className="relative flex h-full max-w-full flex-col border-l border-hairline bg-panel animate-slide-in" style={{ width }}>
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-3">
          <div className="min-w-0">
            {kicker ? <div className="micro mb-0.5">{kicker}</div> : null}
            <div className="truncate text-[14px] font-medium text-ink">{title}</div>
          </div>
          <button type="button" onClick={onClose} className="micro rounded-xs border border-hairline-2 px-1.5 py-0.5 hover:border-muted hover:text-ink">
            esc
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <footer className="border-t border-hairline px-5 py-3">{footer}</footer> : null}
      </aside>
    </div>
  );
}
