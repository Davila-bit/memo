"use client";

import { useEffect } from "react";
import { IS_STATIC, withBase } from "@/lib/base";

/** Registers /sw.js (push + notification clicks only; no offline caching of data). */
export function ServiceWorker() {
  useEffect(() => {
    if (IS_STATIC || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register(withBase("/sw.js"), { scope: withBase("/") }).catch(() => {
      /* not fatal: the app works without it */
    });
  }, []);
  return null;
}
