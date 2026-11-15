# hearth-agent

The check-in helper: a livekit-agents worker (`hearth-agent`, explicit dispatch only) that runs one
check-in call per job with Gemini Live. It hears her, sees her camera, speaks, and calls tools that
POST to the core's `/internal` endpoints. Its own video is the cue card. Contract: `contracts/API.md` §6–§8.

Nothing from a call is written to disk: no frames, audio or transcripts. Logs carry event names,
timings and sizes only.

## Run

```bash
scripts/dev-agent                    # worker in the Coder workspace, keys from fnox
scripts/ws 'cd agent && uv run pytest -q'    # unit tests, no keys needed
```

Env: `GEMINI_API_KEY`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (required);
`HEARTH_INTERNAL_TOKEN` (default `dev-internal`), `GEMINI_LIVE_MODEL` (default `gemini-3.8-live`),
`GEMINI_LIVE_VOICE` (default `Aoede`), `HEARTH_AGENT_NAME` (default `hearth-agent`),
`HEARTH_LOAD_THRESHOLD` (0.95), `HEARTH_MOTION_MIN` (1.5), `HEARTH_PRELIVE_TIMEOUT` (240 s).

## One job, in order

1. Parse the dispatch metadata as an `AgentJob`. Fetch the cue photo into memory.
2. Connect, then publish the **cue card** (camera source, 1280×720, 8 fps, simulcast off,
   `MAINTAIN_RESOLUTION`) and, straight after, the **agent audio track**. RoomIO normally publishes
   audio only once a remote participant is linked; we start that same audio output early and make
   RoomIO's own later start a no-op, so there is always exactly one audio track. Measured: the
   participant shows one unmuted audio track + one video track about 1.5 s after dispatch.
3. Stay silent. RoomIO is linked to nobody, and her video is gated, so nothing reaches the model.
4. **Live** = room metadata `{"hearth_live": true, "checkin_id": …}` (fallback: `GET
   /internal/checkins/{id}` says `connected`). Then link her (identity starts with
   `caller_identity_prefix`), open her video (any track source, about 1 fps), and greet.
   Measured: first speech 0.55–0.86 s after live (agent log). The fake caller heard audio
   0.66–1.17 s after live.
5. Tools → `/internal`. The core's `say` goes back to the model as the tool result. Captions are
   POSTed as `AgentCaption` for both speakers.
6. Goodbye first, then `POST end` (ending hangs up the call), then disconnect. At 80 s the helper is
   told to say goodbye. At `max_seconds` it ends with `time_budget`. Every exit POSTs `end`; a crash
   POSTs `end` with reason `error`.

Safety: the model calls `flag_concern`. A keyword backup on her transcript (`hearth_agent/safety.py`)
also watches. If the model hasn't flagged within 3 s, the backup POSTs `flag_concern` with
`detected_by="keyword"`, makes the helper say the concern line, and ends with `concern`.

Seeing: a confirmation counts as visual only when frames of her are arriving **and** the picture
moved during the last 25 s (mean change of a 32×24 grey thumbnail ≥ `HEARTH_MOTION_MIN`). Otherwise
confidence is capped at 0.4 and the evidence starts with `Said:`, so the core stores a note, not a
confirmation. This stops a still photo, or a cup held up and never drunk from, from being logged as a
drink. If no video arrives within 8 s of live, the helper says it can't see her but can hear her.

## Test harness (`dev/`)

| File | What |
|---|---|
| `stub_api.py` | `/internal` in memory on port 8103. Prints the endpoint, fields and text **lengths** (never caption, evidence or said text). `GET /stub/log` returns the same log as JSON |
| `dispatch.py` | Creates room `agent-test-…`, dispatches with an `AgentJob`, sets `hearth_live` after N s, prints the dial condition, latency and stub log, then deletes the room |
| `fake_caller.py` | Joins as `kiosk-test` with a camera track (generated frames, a still photo, or none) and a mic track. It plays one WAV line each time the helper finishes speaking |
| `make_voices.py` | Makes her test lines with Kokoro (if reachable) or Gemini TTS into `dev/audio/` (gitignored) |
| `render_cards.py` | Writes every cue-card state to PNG for a visual check |
| `live_probe.py` | Checks that a Gemini Live session opens on the key |

```bash
# terminal 1, 2: stub API and a worker under a test-only name (so real dispatches never hit it)
scripts/ws 'cd agent && uv run uvicorn dev.stub_api:app --port 8103'
HEARTH_AGENT_NAME=agent-test-hearth scripts/dev-agent
# terminal 3: a scenario (refusal | concern | novideo | still | budget), keys via fnox
fnox exec --only LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET -- env \
  WS_ENV=LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET,HEARTH_AGENT_NAME HEARTH_AGENT_NAME=agent-test-hearth \
  scripts/ws 'cd agent && uv run python dev/dispatch.py --scenario refusal'
```

The `still` scenario needs `dev/fixtures/kitchen_mug.jpg`: "Middle-aged woman enjoys a warm drink in a
cozy kitchen", Wikimedia Commons, CC BY 2.0. It is gitignored. Download the 960 px version from Commons.

### A human test with real sipping (from a browser)

1. Start the stub and the test worker (terminals 1 and 2 above).
2. Run `dispatch.py --no-caller --live-after 40 --hold 120` (plus `--kind med` or `--kind meal` if
   you like). It prints a `https://meet.livekit.io/custom?…` link holding a 30-minute join token for
   that one room, with identity `kiosk-browser`.
3. Open the link, allow camera and mic, and join before the 40 s are up. You'll see the cue card and
   hear the greeting once live is set. Hold up a cup, drink, and show it again. The stub log shows
   `confirm_drink` and the worker log shows `motion check: max change …`, which is useful for tuning
   `HEARTH_MOTION_MIN` on a real camera.

## Credits

livekit-agents and livekit-plugins-google 1.8.12 (Apache-2.0), LiveKit Python SDK (Apache-2.0),
google-genai (Apache-2.0), Gemini Live `gemini-3.8-live` (Google), Pillow (MIT-CMU), httpx (BSD-3),
pydantic (MIT), FastAPI and uvicorn (MIT/BSD, dev only), numpy (BSD-3, dev only). The Fraunces and
Inter fonts are under the SIL Open Font License 1.1 (`assets/fonts/OFL-*.txt`). The test voices come
from Gemini TTS `gemini-3.8-flash-lite-tts`.
