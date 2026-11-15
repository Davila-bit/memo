#!/usr/bin/env bash
# Chert line diagnostics. Read-only unless the subcommand is `attempt` (places ONE real call).
#   scripts/chert-diagnose.sh lines|gate|raw /workers|call <id>   host python, read-only
#   scripts/chert-diagnose.sh rooms                                  LiveKit room list (workspace container)
#   [VIDEO=1] [WAIT_GATE_S=600] scripts/chert-diagnose.sh attempt   one outbound call to the iPad, with
#        LiveKit-side observation of the bridge; refuses unless line available + session accepting.
# Prints no secrets; masks numbers. Writes no media.
set -euo pipefail
cd "$(dirname "$0")/.."
CH=CHERT_API_KEY,CHERT_PROJECT_ID,CHERT_LINE_ID
LKK=LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET
case "${1:-lines}" in
  rooms)
    fnox exec --only "$CH,$LKK" -- env WS_ENV="$CH,$LKK" scripts/ws \
      'uv run -q --no-project --with livekit --with livekit-api python -I scripts/chert_diagnose.py rooms' ;;
  attempt)
    fnox exec --only "$CH,$LKK,CHERT_IPAD_NUMBER" -- env DRY="${DRY:-0}" VIDEO="${VIDEO:-0}" WAIT_GATE_S="${WAIT_GATE_S:-0}" \
      RING_S="${RING_S:-35}" ACTIVE_S="${ACTIVE_S:-40}" bash -c '
      D="${CHERT_IPAD_NUMBER//[^0-9+]/}"; [[ $D == +* ]] || { [[ ${#D} == 10 ]] && D="+1$D" || D="+$D"; }
      export DEST="$D"; unset CHERT_IPAD_NUMBER
      WS_ENV="'"$CH,$LKK"',DEST,DRY,VIDEO,WAIT_GATE_S,RING_S,ACTIVE_S" exec scripts/ws \
        "uv run -q --no-project --with livekit --with livekit-api python -I scripts/chert_diagnose.py attempt"' ;;
  *)
    fnox exec --only "$CH" -- python3 -I scripts/chert_diagnose.py "$@" ;;
esac
