"use client";

import type { ReactNode } from "react";
import { Rail } from "@/components/shell/Rail";
import { TopStrip } from "@/components/shell/TopStrip";
import { GlobalStreamProvider } from "@/components/shell/GlobalStream";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <GlobalStreamProvider>
      <div className="flex h-screen w-full overflow-hidden bg-canvas text-ink">
        <Rail />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopStrip />
          <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </GlobalStreamProvider>
  );
}
