"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Bottom sheet on phones, centred dialog on wider screens. Radix handles focus trap + Escape. */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/40 data-[state=open]:animate-[fade-in_160ms_ease-out]" />
        <Dialog.Content
          className={cn(
            "fixed z-50 flex max-h-[92dvh] flex-col overflow-y-auto bg-surface shadow-[0_-12px_40px_-12px_rgb(31_42_46/0.35)] outline-none",
            "inset-x-0 bottom-0 rounded-t-[1.75rem] px-5 pt-3 pb-[max(1.5rem,env(safe-area-inset-bottom))]",
            "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:w-[min(32rem,calc(100vw-3rem))] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-[1.5rem] sm:p-7",
            className,
          )}
        >
          <div aria-hidden className="mx-auto mb-3 h-1.5 w-10 rounded-full bg-line sm:hidden" />
          <div className="flex items-start justify-between gap-4">
            <Dialog.Title className="font-serif text-2xl leading-tight font-medium text-ink">{title}</Dialog.Title>
            <Dialog.Close
              className="-mt-1 -mr-2 inline-flex size-11 shrink-0 items-center justify-center rounded-full text-muted hover:bg-ink/[0.06] hover:text-ink"
              aria-label="Close"
            >
              <X aria-hidden className="size-5" weight="bold" />
            </Dialog.Close>
          </div>
          {description ? (
            <Dialog.Description className="mt-1 text-[0.9375rem] text-muted">{description}</Dialog.Description>
          ) : (
            <Dialog.Description className="sr-only">{title}</Dialog.Description>
          )}
          <div className="mt-5">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
