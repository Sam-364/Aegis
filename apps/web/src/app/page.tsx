"use client";

import Link from "next/link";
import { useMemo } from "react";
import { IncidentTable } from "@/components/incident/IncidentTable";
import { EventTicker, PendingApprovalsRail, ReadinessStrip, StatsStrip, TopologyMiniMap } from "@/components/overview/Widgets";
import { ProblemBanner } from "@/components/ui/Problem";
import { Section } from "@/components/ui/Section";
import { Skeleton } from "@/components/ui/Loading";
import { useIncidents } from "@/lib/hooks";
import type { Severity } from "@/lib/types";

const SEV_ORDER: Record<Severity, number> = { sev1: 0, sev2: 1, sev3: 2, sev4: 3 };

export default function OverviewPage() {
  const active = useIncidents({ active: true, limit: 100 }, { refetchInterval: 10_000 });
  const recent = useIncidents({ limit: 8 }, { refetchInterval: 30_000 });
  const items = useMemo(() => (active.data?.items ?? []).slice().sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || b.number - a.number), [active.data]);
  const highlight = useMemo(() => new Set(items.flatMap((i) => i.affected_services)), [items]);
  const recentClosed = (recent.data?.items ?? []).filter((i) => !items.some((a) => a.id === i.id)).slice(0, 5);

  return (
    <div className="flex min-h-full flex-col">
      <StatsStrip />
      <ReadinessStrip />
      <div className="grid flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 border-r border-hairline">
          <Section title="Active incidents" meta={`${items.length} by severity`} live={items.length > 0} flush actions={<Link href="/incidents" className="micro hover:text-ink">all incidents →</Link>}>
            {active.error ? <ProblemBanner error={active.error} className="m-3" onRetry={() => void active.refetch()} /> : active.isPending ? <Skeleton rows={3} /> : <IncidentTable incidents={items} emptyTitle="No active incidents" emptyHint="The detector is watching the simulator. Inject a fault from the Simulation page to exercise the runtime." />}
          </Section>
          <Section title="Recently closed" meta={`${recentClosed.length}`} flush>
            {recent.isPending ? <Skeleton rows={2} /> : <IncidentTable incidents={recentClosed} compact emptyTitle="No history yet" />}
          </Section>
          <EventTicker />
        </div>
        <aside className="min-w-0">
          <PendingApprovalsRail />
          <TopologyMiniMap highlight={highlight} />
        </aside>
      </div>
    </div>
  );
}
