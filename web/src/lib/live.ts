"use client";

// useLive(): one connection to the live channel (/ws, contracts/API.md §5) with reconnect +
// backoff, and polling of /api/today whenever the socket is down (1.5 s while a check-in is
// active, 10 s otherwise). The UI reads everything from the store, so it behaves the same either
// way; only captions need the socket.
import { useEffect } from "react";
import { api, ApiError } from "./api";
import type { WsMessage } from "./contracts";
import { IS_MOCK, wsUrl } from "./env";
import { isActive } from "./format";
import { subscribeMock } from "./mock";
import { useHearth } from "./store";

const POLL_ACTIVE_MS = 1500;
const POLL_IDLE_MS = 10_000;

let refreshing: Promise<void> | null = null;
let refreshQueued = false;

/** Refetch /api/today (coalesced), and resolve a check-in that finished while we weren't looking. */
export async function refreshToday(): Promise<void> {
  if (refreshing) {
    refreshQueued = true;
    return refreshing;
  }
  refreshing = (async () => {
    const st = useHearth.getState();
    try {
      const prev = st.checkin;
      const t = await api.today();
      st.setToday(t);
      if (prev && isActive(prev) && !t.active_checkin) {
        try {
          st.setCheckin(await api.checkin(prev.id));
        } catch {
          /* keep the last known copy */
        }
      }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        st.setLoadError(e instanceof Error ? e.message : "Couldn't reach Hearth.");
      }
    }
  })();
  try {
    await refreshing;
  } finally {
    refreshing = null;
    if (refreshQueued) {
      refreshQueued = false;
      void refreshToday();
    }
  }
}

export async function loadAll(): Promise<void> {
  const st = useHearth.getState();
  await refreshToday();
  try {
    const [schedule, handled] = await Promise.all([api.schedule(), api.alerts("handled")]);
    st.setSchedule(schedule);
    st.setHandledAlerts(handled);
  } catch {
    /* optional extras; Today still renders */
  }
}

function handle(m: WsMessage): void {
  const st = useHearth.getState();
  st.applyMessage(m);
  if (m.type === "event") {
    st.announce(`New: ${m.event.summary}`);
    void refreshToday();
  } else if (m.type === "alert" || m.type === "clock") {
    void refreshToday();
  } else if (m.type === "checkin" && m.checkin.status === "ended") {
    void refreshToday();
  }
}

export function useLive(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let socket: WebSocket | null = null;
    let unsubscribeMock: (() => void) | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    const st = useHearth.getState;

    const schedulePoll = () => {
      if (pollTimer) clearTimeout(pollTimer);
      if (stopped) return;
      const live = st().connection === "live";
      const active = isActive(st().checkin);
      // With the socket up we still refresh occasionally, so totals never drift.
      const ms = live ? 30_000 : active ? POLL_ACTIVE_MS : POLL_IDLE_MS;
      pollTimer = setTimeout(async () => {
        await refreshToday();
        schedulePoll();
      }, ms);
    };

    // Re-arm the poll quickly when a check-in starts.
    const unsubStore = useHearth.subscribe((s, prev) => {
      if (isActive(s.checkin) !== isActive(prev.checkin) || s.connection !== prev.connection) schedulePoll();
    });

    const connect = async () => {
      if (stopped) return;
      if (IS_MOCK) {
        unsubscribeMock = subscribeMock(handle);
        st().setConnection("live");
        return;
      }
      st().setConnection(attempt === 0 ? "connecting" : "polling");
      let ticket: string | null = null;
      try {
        ticket = (await api.wsTicket()).ticket;
      } catch {
        ticket = null; // fall back to the cookie on same-origin sockets
      }
      if (stopped) return;
      try {
        socket = new WebSocket(wsUrl(ticket));
      } catch {
        retry();
        return;
      }
      socket.onopen = () => {
        attempt = 0;
        st().setConnection("live");
        void refreshToday();
      };
      socket.onmessage = (e) => {
        try {
          handle(JSON.parse(String(e.data)) as WsMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        socket = null;
        if (!stopped) retry();
      };
      socket.onerror = () => socket?.close();
    };

    const retry = () => {
      st().setConnection("polling");
      attempt += 1;
      const ms = Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5)) * (0.75 + Math.random() * 0.5);
      reconnectTimer = setTimeout(connect, ms);
    };

    void loadAll().then(schedulePoll);
    void connect();

    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshToday();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      unsubStore();
      document.removeEventListener("visibilitychange", onVisible);
      if (pollTimer) clearTimeout(pollTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      unsubscribeMock?.();
      socket?.close();
    };
  }, [enabled]);
}
