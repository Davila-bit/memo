export const IS_MOCK = process.env.NEXT_PUBLIC_HEARTH_MOCK === "1";
export const IS_DEV = process.env.NODE_ENV === "development";

/** Demo controls (transport picker, demo clock) show in development or with ?demo=1. */
export function demoEnabled(): boolean {
  if (IS_DEV || IS_MOCK) return true;
  if (typeof window === "undefined") return false;
  try {
    const q = new URLSearchParams(window.location.search).get("demo");
    if (q === "1") sessionStorage.setItem("hearth.demo", "1");
    if (q === "0") sessionStorage.removeItem("hearth.demo");
    return sessionStorage.getItem("hearth.demo") === "1";
  } catch {
    return false;
  }
}

export function wsUrl(ticket: string | null): string {
  const explicit = process.env.NEXT_PUBLIC_HEARTH_WS_URL;
  let base: string;
  if (explicit) {
    base = explicit;
  } else {
    const { protocol, hostname, host } = window.location;
    const ws = protocol === "https:" ? "wss:" : "ws:";
    const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
    base = local ? `${ws}//${hostname}:8000/ws` : `${ws}//${host}/ws`;
  }
  if (!ticket) return base;
  return `${base}${base.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`;
}
