import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = (p: P) => ({ width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.25, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, ...p });

export const IconOverview = (p: P) => (
  <svg {...base(p)}>
    <rect x="1.5" y="1.5" width="5.5" height="5.5" />
    <rect x="9" y="1.5" width="5.5" height="5.5" />
    <rect x="1.5" y="9" width="5.5" height="5.5" />
    <rect x="9" y="9" width="5.5" height="5.5" />
  </svg>
);
export const IconIncidents = (p: P) => (
  <svg {...base(p)}>
    <path d="M1.5 8h2.5l1.5-4 2.5 8 1.5-6 1 2h4" />
  </svg>
);
export const IconApprovals = (p: P) => (
  <svg {...base(p)}>
    <rect x="2" y="2" width="12" height="12" rx="1" />
    <path d="M5 8l2 2 4-4" />
  </svg>
);
export const IconTopology = (p: P) => (
  <svg {...base(p)}>
    <rect x="1.5" y="6" width="4" height="4" />
    <rect x="10.5" y="1.5" width="4" height="4" />
    <rect x="10.5" y="10.5" width="4" height="4" />
    <path d="M5.5 8h2.5l2.5-4.5M8 8l2.5 4.5" />
  </svg>
);
export const IconAgent = (p: P) => (
  <svg {...base(p)}>
    <rect x="3.5" y="3.5" width="9" height="9" rx="1" />
    <rect x="6.5" y="6.5" width="3" height="3" />
    <path d="M6 1.5v2M10 1.5v2M6 12.5v2M10 12.5v2M1.5 6h2M1.5 10h2M12.5 6h2M12.5 10h2" />
  </svg>
);
export const IconFlows = (p: P) => (
  <svg {...base(p)}>
    <circle cx="3" cy="3" r="1.5" />
    <circle cx="3" cy="13" r="1.5" />
    <circle cx="13" cy="8" r="1.5" />
    <path d="M3 4.5v7M4.5 3h3a3 3 0 0 1 3 3v0a2 2 0 0 0 2 2M4.5 13h3a3 3 0 0 0 3-3v0" />
  </svg>
);
export const IconTools = (p: P) => (
  <svg {...base(p)}>
    <path d="M9.5 2.5a3.5 3.5 0 0 0 4 4l-7 7a1.5 1.5 0 0 1-2-2l7-7" />
    <path d="M2.5 9.5l4 4" />
  </svg>
);
export const IconPolicies = (p: P) => (
  <svg {...base(p)}>
    <path d="M8 1.5l5.5 2.5v4c0 3-2.5 5.5-5.5 6.5C5 13.5 2.5 11 2.5 8V4z" />
    <path d="M5.5 8h5M8 5.5v5" />
  </svg>
);
export const IconMemory = (p: P) => (
  <svg {...base(p)}>
    <ellipse cx="8" cy="3.5" rx="5.5" ry="2" />
    <path d="M2.5 3.5v9c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-9M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" />
  </svg>
);
export const IconSimulation = (p: P) => (
  <svg {...base(p)}>
    <path d="M6 1.5h4M7 1.5v5l-4.5 7a1 1 0 0 0 .9 1.5h9.2a1 1 0 0 0 .9-1.5L9 6.5v-5" />
    <path d="M4.5 11h7" />
  </svg>
);
export const IconSystem = (p: P) => (
  <svg {...base(p)}>
    <path d="M2 4h12M2 8h12M2 12h12" />
    <rect x="4" y="2.5" width="2.5" height="3" fill="#0b0d10" />
    <rect x="9" y="6.5" width="2.5" height="3" fill="#0b0d10" />
    <rect x="5.5" y="10.5" width="2.5" height="3" fill="#0b0d10" />
  </svg>
);
export const IconExternal = (p: P) => (
  <svg {...base({ width: 12, height: 12, ...p })}>
    <path d="M6.5 3H3v10h10V9.5M9 3h4v4M13 3L7.5 8.5" />
  </svg>
);
export const IconChevron = (p: P) => (
  <svg {...base({ width: 12, height: 12, ...p })}>
    <path d="M5 3l5 5-5 5" />
  </svg>
);

export function AegisMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-label="Aegis">
      <path d="M16 4 L27 9 V17 C27 23 22 27 16 29 C10 27 5 23 5 17 V9 Z" fill="none" stroke="#f5b31a" strokeWidth="1.6" />
      <path d="M11 17 h3 l2 -5 l2 8 l2 -3 h3" fill="none" stroke="#f5b31a" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
