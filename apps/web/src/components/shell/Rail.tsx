"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType, SVGProps } from "react";
import { AegisMark, IconAgent, IconApprovals, IconFlows, IconIncidents, IconMemory, IconOverview, IconPolicies, IconSimulation, IconSystem, IconTools, IconTopology } from "@/components/shell/Icons";
import { useIncidentStats } from "@/lib/hooks";

interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  badge?: "approvals" | "active";
}

const NAV: NavItem[][] = [
  [
    { href: "/", label: "Overview", icon: IconOverview },
    { href: "/incidents", label: "Incidents", icon: IconIncidents, badge: "active" },
    { href: "/approvals", label: "Approvals", icon: IconApprovals, badge: "approvals" },
    { href: "/topology", label: "Topology", icon: IconTopology },
    { href: "/agent-runs", label: "Agent runs", icon: IconAgent },
  ],
  [
    { href: "/flows", label: "Flows", icon: IconFlows },
    { href: "/tools", label: "Tools", icon: IconTools },
    { href: "/policies", label: "Policies", icon: IconPolicies },
    { href: "/memory", label: "Memory", icon: IconMemory },
  ],
  [
    { href: "/simulation", label: "Simulation", icon: IconSimulation },
    { href: "/system", label: "System", icon: IconSystem },
  ],
];

export function Rail() {
  const pathname = usePathname();
  const stats = useIncidentStats();
  const pendingApprovals = stats.data?.pending_approvals ?? 0;
  const active = stats.data?.active ?? 0;

  return (
    <nav className="flex h-full w-[76px] shrink-0 flex-col border-r border-hairline bg-panel" aria-label="Primary">
      <Link href="/" className="flex h-11 items-center justify-center border-b border-hairline" title="Aegis">
        <AegisMark size={22} />
      </Link>
      <div className="flex-1 overflow-y-auto py-1">
        {NAV.map((group, gi) => (
          <ul key={gi} className={`py-1 ${gi > 0 ? "border-t border-hairline" : ""}`}>
            {group.map((item) => {
              const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              const Icon = item.icon;
              const badge = item.badge === "approvals" ? pendingApprovals : item.badge === "active" ? active : 0;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={isActive ? "page" : undefined}
                    className={`relative flex flex-col items-center gap-1 py-2 transition-colors ${isActive ? "text-amber" : "text-muted hover:text-ink"}`}
                  >
                    <span className={`absolute top-1.5 bottom-1.5 left-0 w-[2px] ${isActive ? "bg-amber" : "bg-transparent"}`} aria-hidden />
                    <Icon />
                    <span className="font-sans text-[9px] leading-none tracking-[0.06em] uppercase">{item.label}</span>
                    {badge > 0 ? (
                      <span className={`mono absolute top-1 right-2.5 min-w-[14px] rounded-xs px-1 text-center text-[9px] leading-[14px] ${item.badge === "approvals" ? "bg-amber text-canvas" : "bg-panel-3 text-ink"}`}>
                        {badge}
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        ))}
      </div>
      <div className="border-t border-hairline px-2 py-2 text-center">
        <div className="micro-mono !text-[8.5px] text-faint">console</div>
        <div className="mono text-[9px] text-faint">v0.1.0</div>
      </div>
    </nav>
  );
}
