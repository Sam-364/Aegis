"use client";

import { useState } from "react";

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function Primitive({ value }: { value: unknown }) {
  if (value === null) return <span className="text-faint">null</span>;
  if (typeof value === "string") {
    const long = value.length > 160;
    return <span className="text-ink break-words whitespace-pre-wrap">&quot;{long ? `${value.slice(0, 160)}…` : value}&quot;</span>;
  }
  if (typeof value === "number") return <span className="text-sev4">{Number.isInteger(value) ? value : Number(value.toFixed(4))}</span>;
  if (typeof value === "boolean") return <span className="text-sev3">{String(value)}</span>;
  return <span className="text-muted">{String(value)}</span>;
}

function Node({ name, value, depth, collapsedDepth }: { name: string | null; value: unknown; depth: number; collapsedDepth: number }) {
  const [open, setOpen] = useState(depth < collapsedDepth);
  const container = Array.isArray(value) || isObj(value);
  const entries: [string, unknown][] = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : isObj(value) ? Object.entries(value) : [];
  const empty = container && entries.length === 0;
  const label = name != null ? <span className="text-muted">{name}: </span> : null;

  if (!container) {
    return (
      <div className="leading-[1.5]">
        {label}
        <Primitive value={value} />
      </div>
    );
  }
  const bracket = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div className="leading-[1.5]">
      <button type="button" onClick={() => setOpen((o) => !o)} disabled={empty} className="text-left hover:text-amber disabled:cursor-default">
        <span className="inline-block w-3 text-faint">{empty ? "" : open ? "−" : "+"}</span>
        {label}
        <span className="text-muted">
          {bracket[0]}
          {!open || empty ? (
            <span className="text-faint">
              {entries.length} {Array.isArray(value) ? "items" : "keys"}
            </span>
          ) : null}
          {!open || empty ? bracket[1] : ""}
        </span>
      </button>
      {open && !empty ? (
        <div className="ml-3 border-l border-hairline pl-2.5">
          {entries.map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={depth + 1} collapsedDepth={collapsedDepth} />
          ))}
          <div className="text-muted">{bracket[1]}</div>
        </div>
      ) : null}
    </div>
  );
}

/** Collapsible JSON payload viewer in the mono face. */
export function JsonView({ value, collapsedDepth = 1, className = "" }: { value: unknown; collapsedDepth?: number; className?: string }) {
  return (
    <div className={`mono overflow-x-auto rounded-sm border border-hairline bg-canvas p-2.5 text-[11px] ${className}`}>
      <Node name={null} value={value} depth={0} collapsedDepth={collapsedDepth} />
    </div>
  );
}
