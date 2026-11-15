#!/usr/bin/env bash
# Video gate: one short outbound FaceTime VIDEO call to the kitchen iPad (fnox CHERT_IPAD_NUMBER).
# Shows a test cue card on the iPad and reports whether the iPad camera reaches our agent.
# Usage: scripts/chert-video-probe.sh          real call, ~35 s active (costs < 1 FaceTime min)
#        DRY=1 scripts/chert-video-probe.sh    no call: checks the LiveKit publish path + Gemini
# Runs in a container (nothing installed on the host). Prints no secrets. Saves no frames.
set -euo pipefail
cd "$(dirname "$0")/.."
KEYS=LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET,GEMINI_API_KEY,CHERT_API_KEY,CHERT_PROJECT_ID,CHERT_LINE_ID,CHERT_IPAD_NUMBER
fnox exec --only "$KEYS" -- env DRY="${DRY:-0}" CALL_SECONDS="${CALL_SECONDS:-35}" SCRIPTS="$PWD/scripts" bash -c '
set -euo pipefail
DEST="${CHERT_IPAD_NUMBER//[^0-9+]/}"   # E.164: Chert rejects a bare 10-digit number as unverified
if [[ $DEST != +* ]]; then if [[ ${#DEST} == 10 ]]; then DEST="+1$DEST"; else DEST="+$DEST"; fi; fi
export DEST
exec docker run --rm --name hearth-probe-$$ \
  -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -e GEMINI_API_KEY \
  -e CHERT_API_KEY -e CHERT_PROJECT_ID -e CHERT_LINE_ID -e DEST -e DRY -e CALL_SECONDS \
  -v hearth-uv-cache:/root/.cache/uv -v "$SCRIPTS/chert_video_probe.py:/probe.py:ro" \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  uv run --no-project --with livekit --with livekit-api --with numpy --with pillow --with google-genai \
  python /probe.py
'
