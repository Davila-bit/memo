#!/usr/bin/env bash
# Ring a FaceTime device from the Chert line for ~20 s, playing a test tone. Audio only.
# Proves: the destination is a verified recipient, outbound works, audio reaches the device.
# Usage: scripts/chert-ring-test.sh <destination> [phone|email]
#   e.g. scripts/chert-ring-test.sh +14045551234 phone
# Costs < 1 FaceTime minute of the trial. Needs fnox keys: LIVEKIT_*, CHERT_API_KEY,
# CHERT_PROJECT_ID, CHERT_LINE_ID. Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:?destination phone (+1...) or email}"; DTYPE="${2:-phone}"
if [[ $DTYPE == phone ]]; then DEST="${DEST//[^0-9+]/}"; [[ $DEST == +* ]] || { [[ ${#DEST} == 10 ]] && DEST="+1$DEST" || DEST="+$DEST"; }; fi
ROOM="ring-test-$(date +%s)"; AGENT_ID="probe-agent"; BRIDGE_ID="chert-bridge-probe"
CACHE=.cache; mkdir -p "$CACHE"
[[ -f $CACHE/tone.ogg ]] || ffmpeg -loglevel error -f lavfi -i "sine=frequency=440:duration=40" -ac 1 -ar 48000 -c:a libopus "$CACHE/tone.ogg"
LK=livekit/livekit-cli:v2.19.2

fnox exec --only LIVEKIT_URL,LIVEKIT_API_KEY,LIVEKIT_API_SECRET,CHERT_API_KEY,CHERT_PROJECT_ID,CHERT_LINE_ID -- \
  env ROOM="$ROOM" AGENT_ID="$AGENT_ID" BRIDGE_ID="$BRIDGE_ID" DEST="$DEST" DTYPE="$DTYPE" LK="$LK" CACHE="$PWD/$CACHE" bash -c '
set -euo pipefail
cleanup(){ docker rm -f "$ROOM" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# 1. a participant that publishes audio (the stand-in agent)
docker run -d --name "$ROOM" -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -v "$CACHE:/media:ro" \
  "$LK" room join --identity "$AGENT_ID" --publish /media/tone.ogg "$ROOM" >/dev/null
sleep 4
# 2. a call-scoped bridge token (not printed)
TOKEN=$(python3 -I -c "import base64,hashlib,hmac,json,os,time
b=lambda x: base64.urlsafe_b64encode(x).rstrip(b\"=\")
now=int(time.time())
h=b(json.dumps({\"alg\":\"HS256\",\"typ\":\"JWT\"}).encode())
p=b(json.dumps({\"iss\":os.environ[\"LIVEKIT_API_KEY\"],\"sub\":os.environ[\"BRIDGE_ID\"],\"name\":os.environ[\"BRIDGE_ID\"],\"nbf\":now-5,\"exp\":now+900,\"video\":{\"roomJoin\":True,\"room\":os.environ[\"ROOM\"],\"canPublish\":True,\"canSubscribe\":True}}).encode())
s=b(hmac.new(os.environ[\"LIVEKIT_API_SECRET\"].encode(),h+b\".\"+p,hashlib.sha256).digest())
print((h+b\".\"+p+b\".\"+s).decode())")
[[ -n "$TOKEN" ]] || { echo "could not mint bridge token"; exit 1; }
# 3. place the call
BODY=$(TOKEN="$TOKEN" python3 -I -c "import json,os;print(json.dumps({\"direction\":\"outbound\",\"mode\":\"customer_managed\",\"line_id\":os.environ[\"CHERT_LINE_ID\"],\"destination\":os.environ[\"DEST\"],\"destination_type\":os.environ[\"DTYPE\"],\"media\":{\"audio\":True,\"video\":False},\"livekit_url\":os.environ[\"LIVEKIT_URL\"],\"participant_token\":os.environ[\"TOKEN\"],\"remote_participant_identity\":os.environ[\"AGENT_ID\"]}))")
H=(-H "Authorization: Bearer $CHERT_API_KEY" -H "X-Chert-Project-Id: $CHERT_PROJECT_ID" -H "Content-Type: application/json")
RESP=$(curl -s -w "\n%{http_code}" "${H[@]}" -H "Idempotency-Key: $ROOM-create" -d "$BODY" https://facetime.trychert.com/api/v1/calls)
CODE=$(tail -n1 <<<"$RESP"); JSON=$(sed "\$d" <<<"$RESP")
echo "create call: HTTP $CODE"
python3 -I -c "import json,sys;d=json.loads(sys.argv[1]);e=d.get(\"error\") or {};print(\" error:\",e) if e else print(\" call id:\",d.get(\"data\",{}).get(\"id\"),\" status:\",d.get(\"data\",{}).get(\"status\"))" "$JSON" || echo "$JSON" | head -c 300
CALL_ID=$(python3 -I -c "import json,sys;print(json.loads(sys.argv[1]).get(\"data\",{}).get(\"id\",\"\"))" "$JSON" 2>/dev/null || true)
[[ -n "$CALL_ID" ]] || exit 0
# 4. watch for ~25 s, then end
ACTIVE=0
for i in $(seq 1 15); do sleep 3; printf " t+%ss " "$((i*3))"
  curl -s "${H[@]}" "https://facetime.trychert.com/api/v1/calls/$CALL_ID" | python3 -I -c "import json,sys;d=json.load(sys.stdin).get(\"data\",{});print({k:d.get(k) for k in (\"status\",\"end_reason\",\"failure_reason\",\"answered_at\")})" | tee /dev/stderr | grep -q "active" && ACTIVE=$((ACTIVE+1)) || true
  [[ $ACTIVE -ge 4 ]] && break
done
curl -s -o /dev/null -w "end call: HTTP %{http_code}\n" "${H[@]}" -H "Idempotency-Key: $ROOM-end" -d "{}" "https://facetime.trychert.com/api/v1/calls/$CALL_ID/end"
echo "events:"; curl -s "${H[@]}" "https://facetime.trychert.com/api/v1/calls/$CALL_ID/events" | python3 -I -c "import json,sys;d=json.load(sys.stdin);ev=d.get(\"data\",{});ev=ev.get(\"events\",ev.get(\"items\",[])) if isinstance(ev,dict) else ev;[print(\"  \",e.get(\"type\") or e.get(\"event\"),e.get(\"created_at\") or e.get(\"at\")) for e in list(ev or [])[:20]]" || true
'
