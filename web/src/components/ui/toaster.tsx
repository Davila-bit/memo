"use client";

import { CheckCircle, Info, Warning, X } from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useHearth } from "@/lib/store";
import { cn } from "@/lib/cn";

/**
 * Toasts for confirmations ("Check-in started"), plus the app-wide polite live region.
 * Alerts are never toasts: they stay pinned on Today until Sarah acts.
 */
export function Toaster() {
  const toasts = useHearth((s) => s.toasts);
  const drop = useHearth((s) => s.dropToast);
  const announcement = useHearth((s) => s.announcement);
  const reduce = useReducedMotion();
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-[calc(5.5rem+env(safe-area-inset-bottom))] z-50 flex flex-col items-center gap-2 px-4 lg:bottom-6 lg:left-auto lg:right-6 lg:items-end"
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => {
            const I = t.tone === "ok" ? CheckCircle : t.tone === "warn" ? Warning : Info;
            return (
              <motion.div
                key={t.id}
                layout={!reduce}
                initial={reduce ? false : { opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
                className="pointer-events-auto flex max-w-md items-center gap-3 rounded-2xl bg-ink py-2 pr-2 pl-4 text-[0.9375rem] text-surface shadow-lg"
              >
                <I aria-hidden weight="bold" className={cn("size-5 shrink-0", t.tone === "warn" ? "text-amber-fill" : "text-[#9cc7b5]")} />
                <span>{t.text}</span>
                <button
                  type="button"
                  onClick={() => drop(t.id)}
                  className="inline-flex size-11 shrink-0 items-center justify-center rounded-full hover:bg-surface/10"
                  aria-label="Dismiss"
                >
                  <X aria-hidden className="size-4" weight="bold" />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </>
  );
}
