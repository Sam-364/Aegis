import { TONE_COLOR, type Tone } from "@/lib/colors";

export function LiveDot({ tone = "live", pulse = false, size = 6, color, className = "" }: { tone?: Tone; pulse?: boolean; size?: number; color?: string; className?: string }) {
  const c = color ?? TONE_COLOR[tone];
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 rounded-full ${pulse ? "animate-pulse-amber" : ""} ${className}`}
      style={{ width: size, height: size, background: c, ["--tw-shadow-color" as string]: c }}
    />
  );
}
