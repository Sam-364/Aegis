import { AMBER, DANGER, hexWithAlpha, INFO, OK, TONE_COLOR, type Tone } from "@/lib/colors";
import { fmtScore } from "@/lib/format";
import type { HypothesisScore } from "@/lib/types";

export function Meter({
  value,
  max = 1,
  tone = "live",
  color,
  height = 6,
  label,
  showValue = true,
  className = "",
}: {
  value: number;
  max?: number;
  tone?: Tone;
  color?: string;
  height?: number;
  label?: string;
  showValue?: boolean;
  className?: string;
}) {
  const c = color ?? TONE_COLOR[tone];
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {label ? <span className="micro w-28 shrink-0 truncate">{label}</span> : null}
      <div className="relative flex-1 overflow-hidden rounded-xs" style={{ height, background: hexWithAlpha(c, 0.12) }}>
        <div className="h-full rounded-xs transition-[width] duration-500" style={{ width: `${pct}%`, background: c }} />
      </div>
      {showValue ? <span className="mono w-10 shrink-0 text-right text-[11px] text-ink">{fmtScore(value)}</span> : null}
    </div>
  );
}

/** Large confidence readout: number + bar. */
export function ConfidenceMeter({ value, className = "" }: { value: number; className?: string }) {
  const color = value >= 0.8 ? OK : value >= 0.55 ? AMBER : value >= 0.3 ? "#ff8a3d" : DANGER;
  return (
    <div className={className}>
      <div className="flex items-baseline justify-between">
        <span className="micro">Confidence</span>
        <span className="mono text-[22px] leading-none font-medium" style={{ color }}>
          {fmtScore(value)}
        </span>
      </div>
      <div className="mt-1.5 h-[8px] w-full overflow-hidden rounded-xs" style={{ background: hexWithAlpha(color, 0.12) }}>
        <div className="h-full rounded-xs transition-[width] duration-500" style={{ width: `${Math.min(100, value * 100)}%`, background: color }} />
      </div>
    </div>
  );
}

const SCORE_PARTS: { key: keyof Omit<HypothesisScore, "total" | "explanation">; label: string; color: string; signed?: boolean }[] = [
  { key: "evidence_strength", label: "Evidence strength", color: INFO },
  { key: "temporal_alignment", label: "Temporal alignment", color: INFO },
  { key: "dependency_alignment", label: "Dependency alignment", color: INFO },
  { key: "historical_similarity", label: "Historical similarity", color: AMBER },
  { key: "contradiction_penalty", label: "Contradiction penalty", color: DANGER, signed: true },
  { key: "validation_bonus", label: "Validation bonus", color: OK, signed: true },
];

/** Deterministic score breakdown: one bar per component. Penalties render red, bonuses green. */
export function ScoreBreakdown({ score, className = "" }: { score: HypothesisScore; className?: string }) {
  return (
    <div className={`space-y-1.5 ${className}`}>
      {SCORE_PARTS.map((p) => {
        const raw = score[p.key];
        const negative = p.key === "contradiction_penalty" ? raw > 0 : raw < 0;
        const magnitude = Math.min(1, Math.abs(raw));
        const color = negative ? DANGER : p.color;
        const display = p.key === "contradiction_penalty" && raw > 0 ? `-${fmtScore(raw)}` : p.signed && raw > 0 ? `+${fmtScore(raw)}` : fmtScore(raw);
        return (
          <div key={p.key} className="flex items-center gap-2">
            <span className="micro w-36 shrink-0 truncate">{p.label}</span>
            <div className="relative h-[5px] flex-1 overflow-hidden rounded-xs" style={{ background: hexWithAlpha(color, 0.1) }}>
              <div className="h-full rounded-xs" style={{ width: `${magnitude * 100}%`, background: color, opacity: magnitude === 0 ? 0 : 1 }} />
            </div>
            <span className="mono w-12 shrink-0 text-right text-[11px]" style={{ color: magnitude === 0 ? "#5b6270" : color }}>
              {display}
            </span>
          </div>
        );
      })}
    </div>
  );
}
