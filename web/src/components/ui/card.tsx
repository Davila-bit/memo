import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type Tone = "plain" | "ok" | "check" | "urgent" | "info";

const tones: Record<Tone, string> = {
  plain: "bg-surface border-line",
  ok: "bg-sage-tint border-sage/40",
  check: "bg-amber-tint border-amber-fill/50",
  urgent: "bg-coral-tint border-coral/60",
  info: "bg-sky-tint border-sky/40",
};

export interface CardProps extends HTMLAttributes<HTMLElement> {
  tone?: Tone;
  as?: "section" | "article" | "div" | "li";
}

export function Card({ tone = "plain", as: As = "section", className, ...rest }: CardProps) {
  return (
    <As
      className={cn("rounded-[var(--radius-card)] border p-5 shadow-[var(--shadow-card)] sm:p-6", tones[tone], className)}
      {...rest}
    />
  );
}

/** Small uppercase label above a card's content. */
export function Eyebrow({ className, ...rest }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-[0.8125rem] font-semibold uppercase tracking-[0.08em] text-muted", className)} {...rest} />;
}
