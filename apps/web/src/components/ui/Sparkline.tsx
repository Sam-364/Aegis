import { AMBER, hexWithAlpha } from "@/lib/colors";

export function Sparkline({
  values,
  width = 96,
  height = 22,
  color = AMBER,
  baseline,
  fill = true,
  className = "",
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  baseline?: number | null;
  fill?: boolean;
  className?: string;
}) {
  if (values.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden />;
  }
  const all = baseline != null ? [...values, baseline] : values;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const pad = 1.5;
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2);
  const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${d} L${x(values.length - 1).toFixed(1)},${height} L${x(0).toFixed(1)},${height} Z`;
  return (
    <svg width={width} height={height} className={className} aria-hidden>
      {fill ? <path d={area} fill={hexWithAlpha(color, 0.12)} /> : null}
      {baseline != null ? <line x1={0} x2={width} y1={y(baseline)} y2={y(baseline)} stroke="#5b6270" strokeWidth={1} strokeDasharray="2 2" /> : null}
      <path d={d} fill="none" stroke={color} strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={1.75} fill={color} />
    </svg>
  );
}
