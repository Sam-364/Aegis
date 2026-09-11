"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "outline" | "ghost" | "danger" | "danger-solid";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary: "bg-amber text-canvas border-amber hover:bg-[#ffc23a] disabled:hover:bg-amber",
  outline: "bg-transparent text-ink border-hairline-2 hover:border-muted hover:bg-panel-2",
  ghost: "bg-transparent text-muted border-transparent hover:text-ink hover:bg-panel-2",
  danger: "bg-transparent text-sev1 border-[#5a2426] hover:bg-[#2a1416]",
  "danger-solid": "bg-sev1 text-canvas border-sev1 hover:bg-[#ff6466]",
};
const SIZE: Record<Size, string> = {
  sm: "h-6 px-2 text-[11px]",
  md: "h-7 px-2.5 text-[12px]",
  lg: "h-9 px-4 text-[13px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
}

export function Button({ variant = "outline", size = "md", loading = false, className = "", children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center gap-1.5 rounded-sm border font-sans font-medium tracking-[0.01em] whitespace-nowrap transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
    >
      {loading ? <span className="inline-block h-2 w-2 animate-pulse-amber rounded-full bg-current" aria-hidden /> : null}
      {children}
    </button>
  );
}
