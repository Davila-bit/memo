#!/usr/bin/env bash
# Chert line diagnostics. Read-only unless the subcommand is `attempt` (places ONE real call).
#   scripts/chert-diagnose.sh lines|gate|raw /workers|call <id>   host python, read-only
#   scripts/chert-diagnose.sh rooms                                  LiveKit room list (workspace container)
#   [VIDEO=1] [WAIT_GATE_S=600] scripts/chert-diagnose.sh attempt   one outbound call to the iPad, with
#        LiveKit-side observation of the bridge; refuses unless line available + session accepting.
#   scripts/chert-diagnose.sh receive        inbound test: starts (or reuses) a cloudflared quick tunnel,
#        prints the WEBHOOK URL to paste in the Chert dashboard, then runs the BYOR webhook receiver
#        (port 8110 in the workspace container) until the first inbound call ends (max 25 min).
#   scripts/chert-diagnose.sh tunnel-stop    remove the tunnel container (the URL dies with it).
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
  receive)
    WS=$(docker ps -q --filter label=hearth.workspace=true --filter "label=coder.owner=${WS_OWNER:-hearth}" --filter "label=coder.workspace_name=${WS_NAME:-hearth}" | head -1)
    [[ -n $WS ]] || { echo "workspace container not running"; exit 1; }
    TUN=hearth-diag-tunnel
    if [[ -z $(docker ps -q --filter "name=^${TUN}$") ]]; then
      docker rm -f "$TUN" >/dev/null 2>&1 || true
      docker run -d --name "$TUN" --network "container:$WS" cloudflare/cloudflared:2026.3.0 \
        tunnel --no-autoupdate --url http://localhost:8110 >/dev/null
    fi
    URL=""
    for _ in $(seq 1 40); do
      URL=$(docker logs "$TUN" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)
      [[ -n $URL ]] && break; sleep 1
    done
    [[ -n $URL ]] || { echo "tunnel did not report a URL"; docker logs --tail 20 "$TUN"; exit 1; }
    echo "$URL/chert-hook" > .cache/chert/webhook-url.txt
    echo "=================================================================="
    echo "WEBHOOK URL (paste in the Chert dashboard): $URL/chert-hook"
    echo "=================================================================="
    trap 'scripts/ws "pkill -f \"chert_diagnose.py receive\"" >/dev/null 2>&1 || true' EXIT
    fnox exec --only "$CH,$LKK,GEMINI_API_KEY" -- env WS_ENV="$CH,$LKK,GEMINI_API_KEY,ACTIVE_S,MAX_MIN" \
      ACTIVE_S="${ACTIVE_S:-60}" MAX_MIN="${MAX_MIN:-25}" scripts/ws \
      'uv run -q --no-project --with livekit --with livekit-api --with numpy --with pillow --with google-genai --with aiohttp python -I scripts/chert_diagnose.py receive' ;;
  tunnel-stop)
    docker rm -f hearth-diag-tunnel ;;
  *)
    fnox exec --only "$CH" -- python3 -I scripts/chert_diagnose.py "$@" ;;
esac
