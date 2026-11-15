"use client";

import { useEffect } from "react";

/** Registers /sw.js (push + notification clicks only; no offline caching of data). */
export function ServiceWorker() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* not fatal: the app works without it */
    });
  }, []);
  return null;
}
