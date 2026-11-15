"use client";

import { motion, useReducedMotion } from "motion/react";

/** Ring showing ml / target. The numbers are also given as text for screen readers. */
export function HydrationRing({ ml, target, mum }: { ml: number; target: number; mum: string }) {
  const reduce = useReducedMotion();
  const pct = target > 0 ? Math.min(1, ml / target) : 0;
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative size-32 shrink-0 sm:size-44" role="img" aria-label={`${mum} has had ${Math.round(ml)} of ${target} ml today, ${Math.round(pct * 100)} percent of her goal.`}>
      <svg viewBox="0 0 120 120" className="size-full -rotate-90" aria-hidden data-ring>
        <circle cx="60" cy="60" r={r} fill="none" stroke="var(--sky-tint)" strokeWidth="10" />
        <motion.circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke="var(--sky)"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={c}
          initial={reduce ? false : { strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - pct) }}
          transition={{ duration: reduce ? 0 : 1.1, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
      <div aria-hidden className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-serif text-[2.25rem] sm:text-[2.75rem] leading-none font-medium tabular">{Math.round(ml)}</span>
        <span className="mt-1 text-[0.8125rem] text-muted sm:text-sm">of {target} ml</span>
      </div>
    </div>
  );
}
