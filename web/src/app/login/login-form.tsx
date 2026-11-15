"use client";

import { WarningCircle } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import { useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK } from "@/lib/env";
import { cn } from "@/lib/cn";

export function LoginForm() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const errorId = useId();
  const hintId = useId();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!code.trim()) {
      setError("Please enter your passcode.");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.login({ passcode: code.trim() });
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That passcode didn't work. Please check it and try again."
          : err instanceof Error
            ? err.message
            : "Couldn't reach Memo. Please try again.",
      );
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  return (
    <form onSubmit={submit} noValidate className="mt-8">
      <label htmlFor={inputId} className="block text-[0.9375rem] font-semibold">
        Passcode
      </label>
      <p id={hintId} className="mt-1 text-sm text-muted">
        {IS_MOCK ? "Practice mode: the passcode is hearth-demo." : "The family passcode you were given when Memo was set up."}
      </p>
      <input
        ref={inputRef}
        id={inputId}
        name="passcode"
        type="password"
        autoComplete="current-password"
        autoCapitalize="none"
        spellCheck={false}
        value={code}
        onChange={(e) => setCode(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={cn(hintId, error && errorId)}
        className={cn(
          "mt-2 block min-h-14 w-full rounded-2xl border-2 bg-surface px-4 text-lg text-ink transition-colors",
          "focus-visible:border-sky-ink",
          error ? "border-coral-ink" : "border-line-strong/70",
        )}
      />
      <p id={errorId} role={error ? "alert" : undefined} className={cn("mt-2 flex items-start gap-1.5 text-[0.9375rem] font-medium text-coral-ink", !error && "sr-only")}>
        {error ? (
          <>
            <WarningCircle aria-hidden weight="bold" className="mt-0.5 size-5 shrink-0" />
            {error}
          </>
        ) : null}
      </p>
      <Button type="submit" size="lg" className="mt-5 w-full" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
