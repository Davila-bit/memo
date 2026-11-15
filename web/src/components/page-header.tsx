import type { ReactNode } from "react";

/** Standard page heading: one h1 per page, optional one-line intro and actions. */
export function PageHeader({ title, intro, actions }: { title: string; intro?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="font-serif text-[2rem] leading-[1.1] font-medium tracking-tight text-balance sm:text-[2.5rem]">{title}</h1>
        {intro ? <p className="mt-2 max-w-prose text-[1.0625rem] text-muted">{intro}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </div>
  );
}

/** Calm "coming soon" body for pages another engineer will build. */
export function Placeholder({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-[var(--radius-card)] border border-dashed border-line-strong/50 bg-surface/60 px-6 py-14 text-center">
      <div className="text-sage-ink">{icon}</div>
      <p className="mt-4 max-w-sm text-[1.0625rem] text-muted">{children}</p>
    </div>
  );
}
