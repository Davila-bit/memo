"use client";

import { Bell, BellSlash, SignOut, TextAa } from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useHearth } from "@/lib/store";
import { Button, buttonClasses } from "./ui/button";
import { Sheet } from "./ui/sheet";

const LARGE_KEY = "hearth.largeType";

export function useLargeType(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mirrors the attribute set before paint
    setOn(document.documentElement.dataset.largeType === "1");
  }, []);
  const set = (v: boolean) => {
    setOn(v);
    if (v) document.documentElement.dataset.largeType = "1";
    else delete document.documentElement.dataset.largeType;
    try {
      localStorage.setItem(LARGE_KEY, v ? "1" : "0");
    } catch {
      /* private mode */
    }
  };
  return [on, set];
}

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

type PushState = "unknown" | "unsupported" | "no-key" | "off" | "on" | "denied" | "busy";

function usePush(open: boolean) {
  const [state, setState] = useState<PushState>("unknown");
  const [key, setKey] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        if (!cancelled) setState("unsupported");
        return;
      }
      try {
        const { public_key } = await api.pushKey();
        if (cancelled) return;
        if (!public_key) return setState("no-key");
        setKey(public_key);
        if (Notification.permission === "denied") return setState("denied");
        const reg = await navigator.serviceWorker.getRegistration();
        const sub = await reg?.pushManager.getSubscription();
        if (!cancelled) setState(sub ? "on" : "off");
      } catch {
        if (!cancelled) setState("no-key");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const enable = async () => {
    if (!key) return;
    setState("busy");
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") return setState(perm === "denied" ? "denied" : "off");
      const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
      await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) });
      const json = sub.toJSON();
      await api.pushSubscribe({
        endpoint: sub.endpoint,
        expirationTime: sub.expirationTime,
        keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
      });
      setState("on");
    } catch {
      setState("off");
    }
  };
  return { state, enable };
}

export function SettingsSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [large, setLarge] = useLargeType();
  const { state: push, enable } = usePush(open);
  const router = useRouter();
  const reset = useHearth((s) => s.reset);
  const mum = useHearth((s) => s.me?.resident.family_name_for_her ?? "Mum");
  const largeId = useId();
  const largeDesc = useId();

  const signOut = async () => {
    try {
      await api.logout();
    } finally {
      reset();
      onOpenChange(false);
      router.replace("/login");
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Settings">
      <div className="divide-y divide-line">
        <div className="pb-5">
          <Link href="/setup" className={buttonClasses("quiet")} onClick={() => onOpenChange(false)}>Setup</Link>
          <p className="mt-2 text-sm text-muted">Names, daily fluid target and consent.</p>
        </div>
        <div className="flex items-center gap-4 py-5">
          <TextAa aria-hidden className="size-7 shrink-0 text-ink" weight="duotone" />
          <div className="min-w-0 flex-1">
            <p id={largeId} className="font-medium">
              Larger text
            </p>
            <p id={largeDesc} className="text-sm text-muted">
              Makes everything in Memo bigger on this device.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={large}
            aria-labelledby={largeId}
            aria-describedby={largeDesc}
            onClick={() => setLarge(!large)}
            className={cn(
              "relative inline-flex h-11 w-[4.25rem] shrink-0 items-center rounded-full border-2 transition-colors",
              large ? "border-sage-ink bg-sage-ink" : "border-line-strong bg-surface",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "absolute top-1/2 size-8 -translate-y-1/2 rounded-full shadow transition-[left,background-color]",
                large ? "left-[calc(100%-2.25rem)] bg-surface" : "left-1 bg-line-strong",
              )}
            />
            <span className="sr-only">{large ? "On" : "Off"}</span>
          </button>
        </div>

        {push !== "no-key" && push !== "unknown" ? (
          <div className="flex items-start gap-4 py-5">
            {push === "on" ? (
              <Bell aria-hidden className="size-7 shrink-0 text-sage-ink" weight="duotone" />
            ) : (
              <BellSlash aria-hidden className="size-7 shrink-0 text-ink" weight="duotone" />
            )}
            <div className="min-w-0 flex-1">
              <p className="font-medium">Notifications</p>
              <p className="text-sm text-muted">
                {push === "on"
                  ? "On. Memo will tell you when something needs you."
                  : push === "denied"
                    ? "Blocked in your browser settings. Allow notifications for this site to turn them on."
                    : push === "unsupported"
                      ? "This browser can't show notifications. Add Memo to your home screen and try again."
                      : `Get a quiet notification when ${mum} has a drink, and a louder one if something needs you.`}
              </p>
              {push === "off" || push === "busy" ? (
                <Button className="mt-3" variant="sage" onClick={enable} disabled={push === "busy"}>
                  <Bell aria-hidden className="size-5" /> {push === "busy" ? "Turning on…" : "Turn on notifications"}
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="pt-5">
          <Button variant="quiet" onClick={signOut}>
            <SignOut aria-hidden className="size-5" /> Sign out
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
