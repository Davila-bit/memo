"""Chert diagnostics (read-only by default). Prints no secrets; masks phone numbers.

Usage (env CHERT_API_KEY, CHERT_PROJECT_ID, CHERT_LINE_ID via fnox):
  python3 -I scripts/chert_diagnose.py lines            # line + session health summary
  python3 -I scripts/chert_diagnose.py raw /lines       # full JSON of any GET path (masked)
  python3 -I scripts/chert_diagnose.py call <id>        # call + events
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("CHERT_BASE", "https://facetime.trychert.com/api/v1")


def mask(s: str) -> str:
    s = re.sub(r"(?<![\w-])(\+?\d{8,13})(\d\d)(?![\w-])", r"***\2", s)
    for k in ("CHERT_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "CHERT_JOIN_CODE"):
        v = os.environ.get(k)
        if v and len(v) > 4:
            s = s.replace(v, f"<{k}>")
    s = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", s)
    return s


def req(method: str, path: str, body: dict | None = None, idem: str | None = None) -> tuple[int, object]:
    h = {
        "Authorization": f"Bearer {os.environ['CHERT_API_KEY']}",
        "X-Chert-Project-Id": os.environ["CHERT_PROJECT_ID"],
        "Content-Type": "application/json",
        "User-Agent": "hearth-diagnose/0.1",
    }
    if idem:
        h["Idempotency-Key"] = idem
    url = path if path.startswith("http") else BASE + path
    r = urllib.request.Request(url, method=method, headers=h,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return code, json.loads(raw or b"{}")
    except Exception:
        return code, {"raw": raw[:500].decode(errors="replace")}


def dig(d: object, *ks: str) -> object:
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def lines_summary() -> None:
    code, j = req("GET", "/lines")
    items = dig(j, "data")
    if isinstance(items, dict):
        items = items.get("items") or items.get("lines") or [items]
    print("GET /lines HTTP", code)
    for ln in items or []:
        if os.environ.get("CHERT_LINE_ID") and ln.get("id") != os.environ["CHERT_LINE_ID"]:
            continue
        sh = ln.get("session_health") or {}
        rd = sh.get("readiness") or ln.get("readiness") or {}
        out = {
            "status": ln.get("status"),
            "session": sh.get("status"),
            "idle_blocker": dig(sh, "capabilities", "readiness_idle_blocker"),
            "audio_ready": rd.get("audio_ready"),
            "video_ready": rd.get("video_ready"),
            "prod_media": rd.get("production_media_ready"),
            "runtime_started_at": dig(rd, "operating_intent", "runtime_started_at"),
            "active_call": ln.get("active_call_id") or ln.get("current_call_id"),
        }
        print(mask(json.dumps(out)))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "lines"
    if cmd == "lines":
        lines_summary()
    elif cmd == "raw":
        code, j = req("GET", sys.argv[2])
        print("HTTP", code)
        print(mask(json.dumps(j, indent=1)))
    elif cmd == "call":
        cid = sys.argv[2]
        for p in (f"/calls/{cid}", f"/calls/{cid}/events"):
            code, j = req("GET", p)
            print("GET", p, "HTTP", code)
            print(mask(json.dumps(j, indent=1)))
    elif cmd == "gate":
        ok, why = gate()
        print("GATE", "OPEN" if ok else "CLOSED", why)
    elif cmd == "rooms":
        import asyncio
        asyncio.run(lk_rooms())
    elif cmd == "attempt":
        import asyncio
        asyncio.run(attempt())
    elif cmd == "receive":
        import asyncio
        asyncio.run(receive())
    else:
        sys.exit(__doc__)


# ---------------------------------------------------------------- gate (read-only)

def gate() -> tuple[bool, str]:
    """Line available AND session accepting AND worker not draining/offline AND no current call."""
    _, j = req("GET", "/lines")
    items = dig(j, "data", "items") or []
    ln = next((x for x in items if x.get("id") == os.environ.get("CHERT_LINE_ID")), None)
    if not ln:
        return False, "line not listed"
    sh = ln.get("session_health") or {}
    _, w = req("GET", "/workers?limit=100")
    wk = next((x for x in (dig(w, "data", "items") or []) if x.get("id") == ln.get("worker_id")), {})
    why = (f"line={ln.get('status')} session={sh.get('status')} worker={wk.get('status')} "
           f"call={ln.get('current_call_id')} blocker={dig(sh, 'capabilities', 'readiness_idle_blocker')!r} "
           f"runtime={dig(sh, 'readiness', 'operating_intent', 'runtime_started_at')}")
    caps = sh.get("capabilities") or {}
    ok = (ln.get("status") == "available" and sh.get("status") == "accepting"
          and wk.get("status") not in ("draining", "offline") and not ln.get("current_call_id")
          and not caps.get("readiness_idle_blocker") and not ln.get("worker_occupied"))
    return ok, why


# ---------------------------------------------------------------- LiveKit-side observation

def lk_http() -> str:
    return os.environ["LIVEKIT_URL"].replace("wss://", "https://").replace("ws://", "http://")


async def lk_rooms() -> None:
    from livekit import api
    async with api.LiveKitAPI(lk_http(), os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]) as lk:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        print("rooms:", len(rooms.rooms))
        for r in rooms.rooms:
            print(" ", r.name, "participants", r.num_participants, "publishers", r.num_publishers, "meta", r.metadata[:120])


def pinfo(p: object) -> dict:
    tracks = [
        {"name": t.name, "type": int(t.type), "source": int(t.source), "muted": t.muted, "mime": t.mime_type}
        for t in sorted(p.tracks, key=lambda t: t.name)  # type: ignore[attr-defined]
    ]
    perm = p.permission  # type: ignore[attr-defined]
    return {
        "state": int(p.state), "kind": int(p.kind), "tracks": tracks,  # type: ignore[attr-defined]
        "meta": mask(p.metadata)[:300], "attrs": {k: mask(v)[:120] for k, v in p.attributes.items()},  # type: ignore[attr-defined]
        "perm": {"pub": perm.can_publish, "sub": perm.can_subscribe, "data": perm.can_publish_data,
                 "hidden": perm.hidden, "sources": [int(s) for s in perm.can_publish_sources]},
    }


async def attempt() -> None:  # noqa: C901 - linear experiment script
    """One real outbound call with full room + call observation. Env: DEST (E.164), VIDEO=1 optional,
    RING_S (35), ACTIVE_S (40), WAIT_GATE_S (0 = check once; else poll the gate up to that long)."""
    import asyncio
    import math
    import struct
    import time
    from datetime import timedelta

    from livekit import api, rtc

    t0 = time.time()

    def log(*a: object) -> None:
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} [{time.time() - t0:5.1f}s]", *a, flush=True)

    wait_s = int(os.environ.get("WAIT_GATE_S", "0"))
    dry = os.environ.get("DRY") == "1"
    while not dry:
        ok, why = await asyncio.to_thread(gate)
        log("gate", "OPEN" if ok else "CLOSED", why)
        if ok:  # require it to hold on a second look 6 s later (readiness sweeps flap)
            await asyncio.sleep(6)
            ok, why = await asyncio.to_thread(gate)
            log("gate recheck", "OPEN" if ok else "CLOSED", why)
            if ok:
                break
        if time.time() - t0 > wait_s:
            log("gate closed; not placing a call")
            return
        await asyncio.sleep(5)

    video = os.environ.get("VIDEO") == "1"
    room_name = f"diag-{int(time.time())}"
    agent_id, bridge_id = "diag-agent", f"diag-bridge-{int(time.time())}"
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]

    def tok(identity: str, ttl: int = 900) -> str:
        return (api.AccessToken(key, secret).with_identity(identity).with_name(identity)
                .with_ttl(timedelta(seconds=ttl))
                .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True))
                .to_jwt())

    room = rtc.Room()

    def on(ev: str):
        def deco(fn):
            room.on(ev, fn)
            return fn
        return deco

    @on("participant_connected")
    def _pc(p: rtc.RemoteParticipant) -> None:
        log(f"EV participant_connected {p.identity} kind={p.kind} meta={mask(p.metadata)[:200]!r} attrs={dict(p.attributes)}")

    @on("participant_disconnected")
    def _pd(p: rtc.RemoteParticipant) -> None:
        log(f"EV participant_disconnected {p.identity} reason={getattr(p, 'disconnect_reason', '?')}")

    @on("local_track_subscribed")
    def _lts(track: object) -> None:
        log(f"EV local_track_subscribed (a remote subscribed to our track) sid={getattr(track, 'sid', '?')} name={getattr(track, 'name', '?')}")

    @on("track_published")
    def _tp(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log(f"EV track_published by {p.identity}: name={pub.name!r} kind={pub.kind} source={pub.source} muted={pub.muted}")

    @on("track_subscribed")
    def _ts(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log(f"EV track_subscribed from {p.identity}: {pub.name!r} kind={track.kind}")

    @on("track_muted")
    def _tm(p: object, pub: object) -> None:
        log(f"EV track_muted {getattr(p, 'identity', '?')} {getattr(pub, 'name', '?')}")

    @on("track_unmuted")
    def _tu(p: object, pub: object) -> None:
        log(f"EV track_unmuted {getattr(p, 'identity', '?')} {getattr(pub, 'name', '?')}")

    @on("participant_metadata_changed")
    def _pm(p: object, old: str, new: str) -> None:
        log(f"EV participant_metadata_changed {getattr(p, 'identity', '?')}: {mask(new)[:300]!r}")

    @on("participant_attributes_changed")
    def _pa(changed: dict, p: object) -> None:
        log(f"EV participant_attributes_changed {getattr(p, 'identity', '?')}: {{k: mask(v)[:120] for k, v in changed.items()}}")

    @on("room_metadata_changed")
    def _rm(old: str, new: str) -> None:
        log(f"EV room_metadata_changed: {mask(new)[:300]!r}")

    @on("data_received")
    def _dr(pkt: object) -> None:
        d = getattr(pkt, "data", b"") or b""
        log(f"EV data_received from {getattr(getattr(pkt, 'participant', None), 'identity', '?')} topic={getattr(pkt, 'topic', '?')!r} "
            f"{len(d)}B {mask(d[:200].decode(errors='replace'))!r}")

    @on("connection_quality_changed")
    def _cq(p: object, q: object) -> None:
        log(f"EV connection_quality {getattr(p, 'identity', '?')} {q}")

    await room.connect(os.environ["LIVEKIT_URL"], tok(agent_id))
    log(f"agent joined {room_name}")

    asrc = rtc.AudioSource(48000, 1)
    atrack = rtc.LocalAudioTrack.create_audio_track("agent-voice", asrc)
    aopts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    try:  # plain Opus like the known-good lk CLI publisher (no RED redundancy, no DTX)
        aopts.red = False
        aopts.dtx = False
    except Exception:
        pass
    await room.local_participant.publish_track(atrack, aopts)
    tasks: list[asyncio.Task] = []

    async def tone() -> None:
        sr, n, t = 48000, 480, 0
        while True:
            hz = 440 if (t // sr) % 2 == 0 else 554
            data = struct.pack(f"<{n}h", *(int(2500 * math.sin(2 * math.pi * hz * (t + i) / sr)) for i in range(n)))
            t += n
            await asrc.capture_frame(rtc.AudioFrame(data, sr, 1, n))

    tasks.append(asyncio.create_task(tone()))
    if video:
        W, H = 640, 360
        vsrc = rtc.VideoSource(W, H)
        vtrack = rtc.LocalVideoTrack.create_video_track("agent-video", vsrc)
        await room.local_participant.publish_track(vtrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))

        async def frames() -> None:
            n = 0
            while True:
                buf = bytearray(W * H * 4)
                x = (n * 8) % W
                for y in range(H // 2 - 20, H // 2 + 20):
                    off = (y * W + x) * 4
                    buf[off:off + 160] = b"\xff\x80\x30\xff" * 40
                vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, bytes(buf)))
                n += 1
                await asyncio.sleep(0.1)

        tasks.append(asyncio.create_task(frames()))
    log(f"published audio{' + video' if video else ''}")

    lk = api.LiveKitAPI(lk_http(), key, secret)
    seen: dict[str, str] = {}
    room_meta = [""]

    async def observe() -> None:
        while True:
            try:
                ps = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
                now = {p.identity: json.dumps(pinfo(p)) for p in ps.participants}
                for ident, s in now.items():
                    if seen.get(ident) != s:
                        log(f"LK {ident}: {s}")
                for ident in set(seen) - set(now):
                    log(f"LK {ident}: GONE")
                seen.clear()
                seen.update(now)
                rs = await lk.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
                for r in rs.rooms:
                    if r.metadata != room_meta[0]:
                        room_meta[0] = r.metadata
                        log(f"LK room metadata: {mask(r.metadata)[:300]!r}")
            except Exception as e:  # keep observing
                log(f"LK observe error {type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(0.5)

    async def stats() -> None:
        while True:
            await asyncio.sleep(3)
            try:
                st = await atrack.get_stats()
                for s in st:
                    if s.HasField("outbound_rtp"):
                        o = s.outbound_rtp
                        log(f"STATS agent audio outbound bytes_sent={o.sent.bytes_sent} packets={o.sent.packets_sent}")
            except Exception as e:
                log(f"stats unavailable: {type(e).__name__}")
                return

    tasks += [asyncio.create_task(observe()), asyncio.create_task(stats())]
    await asyncio.sleep(3)

    body = {
        "direction": "outbound", "mode": "customer_managed", "line_id": os.environ["CHERT_LINE_ID"],
        "destination": os.environ["DEST"], "destination_type": "phone",
        "media": {"audio": True, "video": video}, "livekit_url": os.environ["LIVEKIT_URL"],
        "participant_token": tok(bridge_id), "remote_participant_identity": agent_id,
    }
    if dry:
        log("DRY=1: no call; observing 6 s")
        await asyncio.sleep(6)
        code, resp = 0, {}
    else:
        code, resp = await asyncio.to_thread(req, "POST", "/calls", body, f"{room_name}-create")
    data = dig(resp, "data") or {}
    call_id = data.get("id") if isinstance(data, dict) else None
    log(f"CREATE HTTP {code} id={call_id} status={data.get('status') if isinstance(data, dict) else None} err={mask(json.dumps(dig(resp, 'error')))}")
    if not call_id:
        for t in tasks:
            t.cancel()
        await room.disconnect()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass
        await lk.aclose()
        return

    ring_s, active_s = int(os.environ.get("RING_S", "35")), int(os.environ.get("ACTIVE_S", "40"))
    created = time.time()
    dialing_at = active_at = None
    last, evseen = None, set()
    terminal = {"ended", "failed", "completed", "canceled", "cancelled", "declined", "no_answer", "busy", "rejected"}
    try:
        while True:
            await asyncio.sleep(1)
            _, r = await asyncio.to_thread(req, "GET", f"/calls/{call_id}")
            d = dig(r, "data") or {}
            st = d.get("status")
            for e in d.get("facetime_call_events") or []:
                if e["id"] not in evseen:
                    evseen.add(e["id"])
                    pl = {k: v for k, v in (e.get("payload") or {}).items() if not k.startswith("__")}
                    log(f"CHERT event {e['event_type']} src={e['source']} at={e['created_at'][11:23]} {mask(json.dumps(pl))[:200]}")
            if st != last:
                log(f"CHERT status {st} media={d.get('media_status')} term={d.get('terminal_reason')}/{d.get('terminal_detail')}")
                last = st
            if st == "dialing" and dialing_at is None:
                dialing_at = time.time()
            if st == "active" and active_at is None:
                active_at = time.time()
            if st in terminal:
                break
            if st == "ending" and time.time() - created > 45:
                log("stuck in ending; stop observing")
                break
            if active_at is None and dialing_at and time.time() - dialing_at > ring_s:
                log("ring budget reached")
                break
            if active_at is None and dialing_at is None and time.time() - created > 45:
                log("never dialled within 45 s")
                break
            if active_at and time.time() - active_at > active_s:
                log("active budget reached")
                break
    finally:
        code, _ = await asyncio.to_thread(req, "POST", f"/calls/{call_id}/end", {}, f"{room_name}-end-{int(time.time())}")
        log(f"END HTTP {code}")
    for _ in range(10):
        await asyncio.sleep(2)
        _, r = await asyncio.to_thread(req, "GET", f"/calls/{call_id}")
        st = dig(r, "data", "status")
        if st in terminal:
            break
    log(f"final call status {st}")
    for t in tasks:
        t.cancel()
    await room.disconnect()
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        pass
    await lk.aclose()
    ok, why = await asyncio.to_thread(gate)
    log("gate after:", why)


# ---------------------------------------------------------------- inbound webhook receiver

async def receive() -> None:  # noqa: C901 - linear experiment script
    """BYOR inbound receiver. A warm room with an agent stand-in (tone + camera card) waits; on
    `call.incoming` we accept at once with a fresh bridge token for that room, then log every track
    the bridge publishes and count caller video frames (memory only). Exits after the first call
    ends, or after MAX_MIN minutes. Env: LIVEKIT_*, CHERT_* (call polling/end), PORT (8110),
    HOOK_PATH (/chert-hook), ACTIVE_S (60: we end the call through the API after this), MAX_MIN (25),
    GEMINI_API_KEY (optional: one frame described in one line)."""
    import asyncio
    import io
    import math
    import struct
    import time
    from datetime import timedelta

    import numpy as np
    from aiohttp import web
    from livekit import api, rtc

    t0 = time.time()

    def log(*a: object) -> None:
        print(f"{time.strftime('%H:%M:%S', time.gmtime())} [{time.time() - t0:6.1f}s]", *a, flush=True)

    port = int(os.environ.get("PORT", "8110"))
    hook_path = os.environ.get("HOOK_PATH", "/chert-hook")
    active_s = int(os.environ.get("ACTIVE_S", "60"))
    max_min = float(os.environ.get("MAX_MIN", "25"))
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    room_name = f"diag-inbound-{int(time.time())}"
    agent_id = "diag-agent"

    def tok(identity: str, ttl: int = 1800) -> str:
        return (api.AccessToken(key, secret).with_identity(identity).with_name(identity)
                .with_ttl(timedelta(seconds=ttl))
                .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True))
                .to_jwt())

    ok, why = await asyncio.to_thread(gate)
    log("line state:", why, "" if ok else "(NOT accepting right now: an incoming call may not reach us)")

    def routing() -> str:
        _, j = req("GET", "/lines")
        ln = next((x for x in (dig(j, "data", "items") or []) if x.get("id") == os.environ.get("CHERT_LINE_ID")), {})
        _, i = req("GET", "/integrations")
        items = dig(i, "data", "items") or []
        urls = [mask(str(x.get("url") or x.get("webhook_url") or ""))[:80] for x in items]
        return (f"line integration_id={'set' if ln.get('integration_id') else None} inbound_policy={ln.get('inbound_policy')} "
                f"assistant={ln.get('default_assistant_id')} routing={ln.get('routing_status')}; integrations={len(items)} {urls}")

    log("routing:", await asyncio.to_thread(routing))

    # --- warm room + agent stand-in
    room = rtc.Room()
    video_stats: dict[str, dict] = {}
    audio_frames: dict[str, int] = {}
    bridge_ids: set[str] = set()
    state: dict = {"call_id": None, "done": asyncio.Event(), "jpeg": None, "accepted_at": None}

    async def watch_video(track: rtc.Track, k: str) -> None:
        s = video_stats.setdefault(k, {"frames": 0, "size": None, "first_at": None, "changed": 0, "luma": None})
        prev = None
        async for ev in rtc.VideoStream(track):
            f = ev.frame
            s["frames"] += 1
            s["size"] = (f.width, f.height)
            if s["first_at"] is None:
                s["first_at"] = time.time()
                log(f"FIRST CALLER VIDEO FRAME on {k}: {f.width}x{f.height}")
            if s["frames"] % 15 == 1:
                rgba = f.convert(rtc.VideoBufferType.RGBA)
                arr = np.frombuffer(rgba.data, dtype=np.uint8).reshape(rgba.height, rgba.width, 4)
                small = arr[::16, ::16, :3].astype(np.int16)
                if prev is not None and prev.shape == small.shape and np.abs(small - prev).mean() > 1.5:
                    s["changed"] += 1
                prev = small
                s["luma"] = int(arr[..., :3].mean())
                if state["jpeg"] is None and s["frames"] > 30:
                    from PIL import Image
                    buf = io.BytesIO()
                    Image.fromarray(arr[..., :3]).save(buf, "JPEG", quality=80)
                    state["jpeg"] = buf.getvalue()  # memory only
                if s["frames"] % 75 == 1:
                    log(f"video {k}: frames={s['frames']} size={s['size']} luma={s['luma']} changed={s['changed']}")

    async def count_audio(track: rtc.Track, k: str) -> None:
        async for _ in rtc.AudioStream(track):
            audio_frames[k] = audio_frames.get(k, 0) + 1

    @room.on("participant_connected")
    def _pc(p: rtc.RemoteParticipant) -> None:
        log(f"EV participant_connected {p.identity}")

    @room.on("participant_disconnected")
    def _pd(p: rtc.RemoteParticipant) -> None:
        log(f"EV participant_disconnected {p.identity}")
        if p.identity in bridge_ids and state["call_id"]:
            asyncio.get_event_loop().call_later(4, state["done"].set)

    @room.on("local_track_subscribed")
    def _lts(track: object) -> None:
        log(f"EV bridge subscribed to our track {getattr(track, 'name', '?')}")

    @room.on("track_published")
    def _tp(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log(f"EV TRACK PUBLISHED by {p.identity}: name={pub.name!r} kind={pub.kind} source={pub.source} "
            f"mime={getattr(pub, 'mime_type', '?')} muted={pub.muted} {getattr(pub, 'width', '')}x{getattr(pub, 'height', '')}")

    @room.on("track_unpublished")
    def _tup(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        log(f"EV track_unpublished by {p.identity}: {pub.name!r}")

    @room.on("track_muted")
    def _tm(p: object, pub: object) -> None:
        log(f"EV track_muted {getattr(p, 'identity', '?')} {getattr(pub, 'name', '?')}")

    @room.on("track_unmuted")
    def _tu(p: object, pub: object) -> None:
        log(f"EV track_unmuted {getattr(p, 'identity', '?')} {getattr(pub, 'name', '?')}")

    @room.on("track_subscribed")
    def _ts(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        k = f"{p.identity}/{pub.name}"
        log(f"EV track_subscribed {k} kind={track.kind}")
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            asyncio.create_task(watch_video(track, k))
        else:
            asyncio.create_task(count_audio(track, k))

    await room.connect(os.environ["LIVEKIT_URL"], tok(agent_id), rtc.RoomOptions(auto_subscribe=True))
    asrc = rtc.AudioSource(48000, 1)
    atrack = rtc.LocalAudioTrack.create_audio_track("agent-voice", asrc)
    aopts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    try:
        aopts.red = False
        aopts.dtx = False
    except Exception:
        pass
    await room.local_participant.publish_track(atrack, aopts)
    W, H = 640, 360
    vsrc = rtc.VideoSource(W, H)
    vtrack = rtc.LocalVideoTrack.create_video_track("agent-video", vsrc)
    await room.local_participant.publish_track(vtrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))

    async def tone() -> None:  # soft two-note chime, never silent (remote_audio_timeout)
        sr, n, t = 48000, 480, 0
        while True:
            hz = 440 if (t // sr) % 2 == 0 else 554
            data = struct.pack(f"<{n}h", *(int(2500 * math.sin(2 * math.pi * hz * (t + i) / sr)) for i in range(n)))
            t += n
            await asrc.capture_frame(rtc.AudioFrame(data, sr, 1, n))

    async def card() -> None:  # warm card with a moving bar so motion is obvious on the iPad
        base = np.empty((H, W, 4), dtype=np.uint8)
        base[...] = (246, 241, 233, 255)
        n = 0
        while True:
            img = base.copy()
            x = (n * 12) % (W - 60)
            img[150:210, x:x + 60] = (200, 72, 59, 255)
            vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, img.tobytes()))
            n += 1
            await asyncio.sleep(0.1)

    bg = [asyncio.create_task(tone()), asyncio.create_task(card())]
    log(f"agent stand-in ready in {room_name} (audio tone + camera card)")

    # --- call observer
    async def observe(call_id: str) -> None:
        seen: set = set()
        active_at = None
        terminal = {"ended", "failed", "completed", "canceled", "cancelled", "declined", "no_answer", "rejected"}
        while True:
            await asyncio.sleep(1)
            _, r = await asyncio.to_thread(req, "GET", f"/calls/{call_id}")
            d = dig(r, "data") or {}
            for e in d.get("facetime_call_events") or []:
                if e["id"] not in seen:
                    seen.add(e["id"])
                    pl = {k: v for k, v in (e.get("payload") or {}).items() if not k.startswith("__")}
                    log(f"CHERT event {e['event_type']} src={e['source']} at={e['created_at'][11:23]} {mask(json.dumps(pl))[:200]}")
            st = d.get("status")
            if st == "active" and active_at is None:
                active_at = time.time()
            if st in terminal:
                log(f"CHERT call {st} terminal={d.get('terminal_reason')}/{d.get('terminal_detail')} media={d.get('media_status')}")
                state["done"].set()
                return
            if active_at and time.time() - active_at > active_s:
                code, _ = await asyncio.to_thread(req, "POST", f"/calls/{call_id}/end", {}, f"diag-in-end-{int(time.time())}")
                log(f"{active_s}s active reached: END HTTP {code}")
                active_at = time.time() + 3600  # do not repeat

    # --- webhook
    async def hook(request: web.Request) -> web.Response:
        rx = time.time()
        raw = await request.read()
        try:
            ev = json.loads(raw or b"{}")
        except Exception:
            ev = {}
        typ = ev.get("type")
        data = ev.get("data") or {}
        sig = [h for h in request.headers if "sig" in h.lower() or "chert" in h.lower()]
        log(f"WEBHOOK {request.method} type={typ} call={data.get('call_id')} media={data.get('media')} headers={sig} keys={sorted(ev)[:12]}")
        if typ == "call.incoming":
            if state["call_id"]:
                log("second incoming while a call is in progress: declining")
                return web.json_response({"action": "decline"})
            bridge = f"diag-bridge-{int(rx)}"
            bridge_ids.add(bridge)
            state["call_id"] = data.get("call_id")
            state["accepted_at"] = time.time()
            body = {"action": "accept", "livekit_url": os.environ["LIVEKIT_URL"],
                    "participant_token": tok(bridge, 900), "remote_participant_identity": agent_id}
            log(f"ACCEPT sent {1000 * (time.time() - rx):.0f} ms after receipt (bridge identity {bridge})")
            if state["call_id"]:
                asyncio.create_task(observe(state["call_id"]))
            return web.json_response(body)
        if typ:
            log(f"webhook event payload: {mask(json.dumps(data))[:300]}")
        return web.json_response({"ok": True})

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post(hook_path, hook)
    app.router.add_get(hook_path, health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log(f"webhook receiver listening on :{port}{hook_path}; waiting up to {max_min:.0f} min for a call")

    try:
        await asyncio.wait_for(state["done"].wait(), timeout=max_min * 60)
    except asyncio.TimeoutError:
        log("no call arrived before MAX_MIN; exiting")
    await asyncio.sleep(2)

    log("=== RESULT ===")
    log("bridge identities:", sorted(bridge_ids) or "none (no call.incoming received)")
    log("caller video tracks:", json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'first_at'} for k, v in video_stats.items()}) if video_stats else "NONE RECEIVED")
    for k, v in video_stats.items():
        if v["first_at"] and state["accepted_at"]:
            log(f"  {k}: first frame {v['first_at'] - state['accepted_at']:.1f}s after accept")
    log("caller audio (frame counts):", audio_frames or "NONE RECEIVED")
    if state["jpeg"] and os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=20_000))
            r = await asyncio.to_thread(
                client.models.generate_content, model="gemini-3.5-flash-lite",
                contents=[types.Part.from_bytes(data=state["jpeg"], mime_type="image/jpeg"),
                          "Describe this video-call frame in one sentence: is there a person, a cup or mug, any call overlay?"])
            log("gemini on one caller frame:", (r.text or "").strip()[:300])
        except Exception as e:
            log(f"gemini skipped: {type(e).__name__}: {str(e)[:150]}")
    state["jpeg"] = None
    for t in bg:
        t.cancel()
    await runner.cleanup()
    await room.disconnect()
    async with api.LiveKitAPI(lk_http(), key, secret) as lk:
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass
    ok, why = await asyncio.to_thread(gate)
    log("line after:", why)


if __name__ == "__main__":
    main()
