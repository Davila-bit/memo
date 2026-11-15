"use client";

import {
  CheckCircle,
  Clock,
  Info,
  Question,
  Warning,
  WarningOctagon,
  type Icon,
} from "@phosphor-icons/react";
import { cn } from "@/lib/cn";

export type Status = "ok" | "check" | "urgent" | "info" | "waiting" | "unknown";

const STYLE: Record<Status, { cls: string; icon: Icon }> = {
  ok: { cls: "bg-sage-tint text-sage-ink border-sage/40", icon: CheckCircle },
  check: { cls: "bg-amber-tint text-amber-ink border-amber-fill/60", icon: Warning },
  urgent: { cls: "bg-coral-tint text-coral-ink border-coral/60", icon: WarningOctagon },
  info: { cls: "bg-sky-tint text-sky-ink border-sky/40", icon: Info },
  waiting: { cls: "bg-surface text-muted border-line-strong/50", icon: Clock },
  unknown: { cls: "bg-surface text-muted border-line-strong/50", icon: Question },
};

/** Status is always icon + word + colour, never colour alone. */
export function StatusBadge({
  status,
  children,
  icon,
  className,
  size = "md",
}: {
  status: Status;
  children: React.ReactNode;
  icon?: Icon;
  className?: string;
  size?: "sm" | "md";
}) {
  const s = STYLE[status];
  const I = icon ?? s.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border font-semibold",
        size === "sm" ? "px-2.5 py-0.5 text-[0.8125rem]" : "px-3 py-1 text-sm",
        s.cls,
        className,
      )}
    >
      <I aria-hidden weight="bold" className={size === "sm" ? "size-3.5" : "size-4"} />
      {children}
    </span>
  );
}
