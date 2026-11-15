# Hearth Home: API, live channel, agent tools, layout

Shapes are defined in `contracts/hearth_contracts/models.py` (reference) and mirrored in
`web/src/lib/contracts.ts`. Module boundaries are in `contracts/hearth_contracts/ports.py`.
**Change this file, the models and the TS mirror in one commit.**

## 1. Repo layout and owners

```
contracts/                     shared shapes + this file           (orchestrator only)
backend/                       uv project "hearth-backend", Python 3.12, FastAPI
  hearth/core/                 app factory, settings, auth, store (Tiger + memory), bus, clock,
                               schedule tick, CheckinService (lifecycle, alerts, no-show chain),
                               REST + /ws + /internal routers, seed data, SQL files
  hearth/calls/                CallService: LiveKit room + agent dispatch + bridge token,
                               Chert Calls API, kiosk transport, mock transport, 90 s watchdog
  hearth/integrations/         Notifier (Web Push + ntfy), Search (Typesense), DigestWriter (Gemini),
                               Redactor (Presidio)
  tests/
agent/                         uv project "hearth-agent": livekit-agents worker `hearth-agent`
web/                           Next.js App Router PWA for the daughter + /kiosk page
infra/coder/                   Coder server + workspace template
scripts/                       ws (run in workspace), probes, dev-up
```

`backend` and `agent` depend on `contracts` as a uv path dependency
(`hearth-contracts = { path = "../contracts", editable = true }`).

## 2. Processes and ports

| Process | Port | Notes |
|---|---|---|
| API (uvicorn `hearth.core.app:create_app --factory`) | 8000 | REST under `/api`, agent-only under `/internal`, live at `/ws`, cue photos at `/media` |
| Web (Next.js) | 3000 | proxies `/api`, `/ws`, `/media` to the API (same origin; cookie auth) |
| Agent worker | none | connects out to LiveKit Cloud; calls the API at `AgentJob.api_base` |

## 3. Environment (names only; values come from `fnox`, never from git)

| Name | Used by | Missing means |
|---|---|---|
| `HEARTH_TRANSPORT` = `facetime` \| `kiosk` \| `mock` (default `mock`) | calls | – |
| `HEARTH_PASSCODE` (default `hearth-demo`), `HEARTH_SESSION_SECRET` | core | dev defaults + a log warning |
| `HEARTH_INTERNAL_TOKEN` | core, agent | dev default `dev-internal` + a log warning |
| `HEARTH_CHERT_HOOK_KEY` | core | unset: all Chert webhook requests return 404; generated once by `scripts/dev-up` |
| `HEARTH_API_BASE` (default `http://127.0.0.1:8000`) | calls → agent job | – |
| `TIGER_DATABASE_URL` | core | in-memory store, seeded |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | calls, agent | only `mock` transport works |
| `CHERT_API_KEY`, `CHERT_PROJECT_ID`, `CHERT_LINE_ID`, `CHERT_IPAD_NUMBER` | calls | `facetime` falls back to `kiosk`, then `mock` |
| `GEMINI_API_KEY` | agent (Live), integrations (digest, ask) | template digest; agent cannot run (use mock) |
| `GEMINI_LIVE_MODEL` (default `gemini-3.8-live`), `GEMINI_TEXT_MODEL` (default `gemini-3.5-flash-lite`) | agent, integrations | – |
| `TYPESENSE_HOST`, `TYPESENSE_API_KEY` | integrations | keyword fallback over recent events |
| `NTFY_TOPIC`, `NTFY_URL` (default `https://ntfy.sh`) | integrations | no urgent push; in-app alert still shows |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | integrations | no Web Push; in-app alert still shows |
| `HEARTH_KIOSK_DEVICE` (default `kitchen`) | calls | – |
| `HEARTH_AGENT_NAME` (default `hearth-agent`), `HEARTH_RING_SECONDS` (default 45), `HEARTH_CHERT_SKIP_LINE_CHECK` | calls, agent | – |
| `HEARTH_PUBLIC_URL` (default `http://localhost:3000`) | notifications | public web origin used in notification links |
| `HEARTH_TIMEZONE` (default `America/New_York`) | integrations | local dates and times for search and digests |
| `HEARTH_REDACT_NAMES` | redactor | comma-separated family names to redact; empty deny-list when missing |
| `NTFY_ALL` (default `0`) | notifications | `1` sends non-urgent notifications to ntfy too |
| `HEARTH_REDACTION` | redactor | `regex` skips Presidio; otherwise load it in the background |
| `TYPESENSE_COLLECTION` (default `hearth_events`) | search | collection used for event indexing and queries |
| `HEARTH_MOCK_SCALE` (default `1`) | core | multiplier for mock call timing |
| `HEARTH_CORS_ORIGINS` | core | comma-separated allowed browser origins; no CORS middleware when empty |

`gemini-3.8-flash` stalled with zero bytes on our free-tier key (checked Sat 23:24), so text work
defaults to `gemini-3.5-flash-lite` (0.5 s).

## 4. REST (`/api`, JSON, cookie session; everything except login and health needs the session)

| Method & path | Body → Response | Purpose |
|---|---|---|
| `GET /api/health` | → `Health` | which integrations are live |
| `POST /api/auth/login` | `LoginRequest` → `Me` (+ `hearth_session` cookie, HttpOnly, SameSite=Lax) | passcode sign-in; 401 on a wrong code |
| `POST /api/auth/logout` | → 204 | |
| `GET /api/me` | → `Me` | 401 when signed out |
| `GET /api/today` | → `Today` | timeline, hydration, next check-in, open alerts |
| `GET /api/week` | → `Week` | 7 days from Tiger continuous aggregates |
| `GET /api/events?kind=&since=&limit=` | → `HearthEvent[]` | newest first |
| `POST /api/events` | `ManualEventRequest` → `HearthEvent` | the daughter logs something herself |
| `POST /api/checkins` | `StartCheckinRequest` → `Checkin` (201) | **"Check on her now"**. 409 if one is already running |
| `GET /api/checkins/{id}` | → `Checkin` | |
| `POST /api/checkins/{id}/cancel` | → `Checkin` | hang up |
| `GET /api/alerts?status=open\|handled\|all` | → `Alert[]` | default `open`; `all` includes open and handled alerts |
| `POST /api/alerts/{id}/action` | `AlertActionRequest` → `Alert` | `retry_later` schedules a retry check-in |
| `GET /api/schedule` · `PUT /api/schedule/{id}` · `POST /api/schedule` · `DELETE /api/schedule/{id}` | `ScheduleItem` | |
| `GET /api/cues` · `PUT /api/cues/{id}` | `Cue` | |
| `POST /api/cues/{id}/photo` | multipart `file` (jpeg/png/webp ≤ 5 MB) → `Cue` | family photo of the mug / plate / pill box |
| `GET /api/resident` · `PUT /api/resident` | `Resident` | first-run setup: names, target, consent |
| `POST /api/ask` | `AskRequest` → `AskResponse` | ask the log |
| `GET /api/digest?day=` | → `Digest` | |
| `GET /api/privacy` | → `Privacy` | |
| `DELETE /api/privacy/data` | → `{deleted_events: int}` | delete everything stored about her |
| `GET /api/demo/clock` · `POST /api/demo/clock` | `ClockRequest` → `Clock` | demo clock |
| `POST /api/demo/reset` | → `Today` | re-seed 6 previous days plus today so far |
| `POST /api/push/subscribe` | `PushSubscription` → 204 | Web Push |
| `GET /api/push/key` | → `{public_key: str \| null}` | VAPID public key |
| `GET /api/ws-ticket` | → `{ticket: str}` | 60-second single-use ticket so the page can open `/ws?ticket=` on another origin |
| `GET /api/kiosk/token?device=` | → `KioskToken` | for the `/kiosk` page; needs `?key=` = `HEARTH_INTERNAL_TOKEN` instead of a session |

Errors: `{"detail": "plain sentence"}` with the right status code.

### Webhooks

| Method & path | Body → Response | Authentication |
|---|---|---|
| `POST /hooks/chert/{key}` | Chert event → accept/decline for `call.incoming`; `{ok: true}` otherwise | constant-time comparison with `HEARTH_CHERT_HOOK_KEY`; wrong or unset key → 404; no session |

`HearthEvent.for_kind: CheckinKind | null` (default `null`) identifies what a `refused`, `missed`,
`concern` or `note` event was for. `Resident.phone: string | null` (default `null`) is her phone
for the daughter's "I'll call her" link; the demo seeds it as `null`.
`PushSubscription` is `{endpoint: string, keys: Record<string, string>}` in both contract mirrors.
Privacy deletion clears stored events, check-ins, alerts and indexed search copies (`Search.clear()`).

## 5. Live channel `GET /ws` (WebSocket; session cookie or `?ticket=`)

Server → client only, one JSON `WsMessage` per frame: `checkin` (every status change), `event`,
`alert` (new or updated), `caption` (live-only, never stored), `clock`. On connect the server sends
the active `checkin` (if any) and a `clock`. The client refetches `/api/today` after reconnecting.

The web app must work without the socket: it falls back to polling `/api/today` every 1.5 s while a
check-in is active (10 s otherwise). Only captions need the socket.

## 6. Agent-only endpoints (`/internal`, header `X-Hearth-Token: $HEARTH_INTERNAL_TOKEN`)

| Method & path | Body → Response | Effect in the core |
|---|---|---|
| `POST /internal/checkins/{id}/status` | `AgentStatus` → `ToolResult` | status `connected` / `watching`, broadcast |
| `POST /internal/checkins/{id}/caption` | `AgentCaption` → 204 | broadcast `caption`; not stored, not logged |
| `POST /internal/checkins/{id}/confirm_drink` | `ToolConfirmDrink` → `ToolResult` | observed `drink` / `confirmed`, or `note` / `reported` (rules below) |
| `POST /internal/checkins/{id}/confirm_meal` | `ToolConfirmMeal` → `ToolResult` | observed `meal` / `confirmed`, or `note` / `reported` |
| `POST /internal/checkins/{id}/confirm_med` | `ToolConfirmMed` → `ToolResult` | observed `med` / `confirmed`, or `note` / `reported` |
| `POST /internal/checkins/{id}/report_refusal` | `ToolReportRefusal` → `ToolResult` | event `refused`, alert (tier `check`), outcome `refused` |
| `POST /internal/checkins/{id}/flag_concern` | `ToolFlagConcern` → `ToolResult` | event `concern`, alert (tier `urgent`), urgent notify, outcome `concern` |
| `POST /internal/checkins/{id}/end` | `ToolEndCheckin` → `ToolResult` | `CallService.end`; status `ended` |
| `GET /internal/checkins/{id}` | → `Checkin` | agent may poll as a fallback "go" signal |

Rules: A confirm with confidence ≥ 0.5 is an observation: event `drink`/`meal`/`med`, outcome
`confirmed`. A confirm with confidence < 0.5 whose evidence starts with `Said:` is a report: stored
as a `note` event with `for_kind` set, outcome `reported`, summary like 'Said she had some water.
The helper couldn't see her on this call.' Any other confirm below 0.5 is stored as a `note` with
no outcome and the reply asks the helper to check once more.
For reports, `said` is the trimmed text after `Said:`; there is no alert, just a normal notification.
Spoken text in `evidence` and `said` passes through `Redactor` before storage; the `Said:` protocol
marker is preserved.
An outcome can be set once; later tool calls for the same check-in return `ok: true` without a new event.

`StartCheckinRequest.mock_outcome` selects the mock ending: `confirmed`, `reported` (she says she
did it; not seen), `refused`, `no_answer`, `concern` or `error`.

## 7. Check-in lifecycle (owned by `CheckinService` in the core)

```
POST /api/checkins ─► queued ─► CallService.start(checkin, job, callbacks)
   call_ringing    ─► ringing
   call_connected  ─► connected      (agent greets only now)
   agent status    ─► watching
   tool call       ─► outcome set + event + (alert) + notify
   end / call_finished ─► ended      (summary sentence written)
```

- **No-show chain:** `call_finished(outcome="no_answer")` on attempt 1 → retry once after 2 minutes
  (demo: `HEARTH_RETRY_SECONDS`, default 120) as attempt 2 → still no answer → event `missed`, alert
  `no_answer` (tier `check`): "Mum didn't answer her 2:00 check-in. Please check on her."
- **90 s budget:** enforced twice, in the agent and in `CallService`. Budget hit with no outcome →
  outcome `error`, event `missed`.
- **`call_finished` semantics:** before live it carries `"no_answer"` or `"error"` + reason. After live
  it always carries `outcome=None`, plus an error string when the end was not clean ("time budget
  reached", a Chert failure). The core then keeps the outcome the agent reported; if none was
  reported it sets `error` itself. Both `confirmed` (observed) and `reported` (said, not seen) are
  completed outcomes kept when the call ends; reports never create a missed event or alert and
  do not count toward fluid totals or observed meal/tablet aggregates.
- `CallService.kiosk_token` may raise `hearth.calls.CallsUnavailable` (plain sentence): map to 503.
  If the service has `aclose()`, the core calls it on shutdown.
- One check-in at a time (the Chert line allows one call).
- The agent never dials 911 and never gives medical advice. `called_911` is only ever set by the
  daughter tapping a `tel:` link in her app.

### Inbound FaceTime

Configure the line's integration as `customer_decides`, with no default Assistant, and point its
unsigned webhook at the URL in `.cache/run/chert-webhook-url` (generated when a public API URL is
available). The path key authenticates this route; Chert signature verification is not implemented.
Never publish this URL or log its key. If webhook signing is enabled, use a receiver that verifies it.

For `{"type":"call.incoming","data":{"call_id":"…", "line_id":"…", "media":{"audio":true,"video":true}}}`,
the core declines when busy or FaceTime is unavailable. Otherwise it creates a FaceTime check-in
with `Checkin.trigger = "inbound"` and dispatches `AgentJob.inbound = true` (default `false`). Kind
and cue come from the nearest enabled schedule time within 90 minutes of the demo clock, including
adjacent days; otherwise use `drink` and its default cue. Events from this check-in use
`EventSource = "inbound"` so the timeline can show "She called"; transport remains `facetime`.

`CallService.accept_inbound(checkin, job, cb, call_id) -> dict` returns
`{action: "accept", livekit_url: "wss://…", participant_token: "<fresh room-scoped bridge JWT>",
remote_participant_identity: "<agent identity>"}` or raises `CallsUnavailable`. It prepares room and
dispatch concurrently, discovers the participant identity without waiting for tracks (up to 2.5 s,
then the configured agent name as fallback), and bounds preparation to 3.5 s. The core bounds the
decision work to 4 s, leaving headroom for Chert's 5 s reply deadline. Errors return a 200 decline
and finish the check-in as `error`; cleanup continues in the background. Duplicate `call_id`s share
the same decision, including declines, for the API process lifetime; the cache is in memory and is
reset on restart. Tokens are never persisted or included in check-in responses.

Acceptance starts the existing placed-call state machine: ringing immediately, then `active` plus
worker `bridge_ready` before setting `hearth_live`, followed by the same budget, end and cleanup.
An inbound call that never becomes ready within `HEARTH_RING_SECONDS` finishes as
`("error", "inbound call never became ready")`. Other webhook event types are acknowledged without
affecting readiness. No real FaceTime call has yet verified this path.

## 8. Agent (`agent/`)

- livekit-agents worker registered as `hearth-agent`, **explicit dispatch only** (no auto-join).
  The dispatch metadata is an `AgentJob` JSON.
- "Her" = the remote participant whose identity starts with `AgentJob.caller_identity_prefix`
  (`chert-bridge-` on FaceTime: tracks `facetime-remote-audio` / `facetime-remote-video`; `kiosk-` on
  the kiosk page). Ignore every other participant.
- The agent publishes exactly one audio track and one camera video track (the cue card), both from
  its own participant, **before** an outbound call is placed (livekit-agents normally publishes audio only
  after a remote participant joins: force it early), and stays silent until live.
- **The "live" signal is ours, not Chert's.** In bring-your-own-room mode nothing writes
  `chert_bridge_ready` into the room. `CallService` decides when the call is live (FaceTime: the call
  is `active` and its events include `bridge_ready` from the worker; kiosk: her participant has an
  audio track) and then sets the LiveKit **room metadata** to
  `{"hearth_live": true, "checkin_id": "<id>"}` and calls `cb.call_connected`. The agent waits for
  that metadata; fallback: `GET /internal/checkins/{id}` says `connected`.
- **Speak within 10 seconds of live.** Chert ends a call whose agent sends no audible audio within
  10 s of `bridge_ready` (`remote_audio_timeout`).
- `CallService` places the call only after `list_participants` shows the agent with one unmuted audio
  track and one video track (poll up to 20 s), and mints the bridge token after that.
  Inbound replies do not wait for published tracks; they use the handshake in §7.
- Cue card (video out, 1280×720): paper background, her object photo, one very large line, live
  caption strip, state changes on tool calls (asking → watching → "Thank you ✓" / "Sarah is coming").
- Tools map 1:1 to §6: `confirm_drink`, `confirm_meal`, `confirm_med`, `report_refusal`,
  `flag_concern`, `end_checkin`. A deterministic keyword check on her transcript backs up
  `flag_concern` (fell, fallen, help, can't get up, chest, can't breathe, numb, weak, dizzy).
- Voice rules: discloses it is "Sarah's helper, an assistant"; short, slow, adult, preferred name, one
  request at a time; asks her to show the cup, drink, show it again; repeats once, never nags; logs
  "observed taking", never "swallowed".
- Nothing from the call is written to disk: no frames, no audio, no transcript files.

## 9. Database (Tiger Cloud; SQL in `backend/hearth/core/sql/`)

`events` hypertable on `ts` (columns = `HearthEvent`), `checkins`, `alerts`, `schedule_items`, `cues`,
`residents`, `push_subscriptions`. Continuous aggregates `fluids_daily`, `adherence_daily` feed
`/api/week`. Retention policy on `events` (90 days) is the number shown on the privacy page. Without
`TIGER_DATABASE_URL` the same `Store` interface runs in memory with the same seed.
