"""REST (/api), agent-only (/internal) and live (/ws) routers."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from hearth_contracts import (
    AgentCaption,
    AgentStatus,
    Alert,
    AlertActionRequest,
    AskRequest,
    AskResponse,
    Checkin,
    Clock,
    ClockRequest,
    Cue,
    Digest,
    Health,
    HearthEvent,
    KioskToken,
    LoginRequest,
    ManualEventRequest,
    Me,
    Privacy,
    PushSubscription,
    Resident,
    Route,
    ScheduleItem,
    StartCheckinRequest,
    StoredThing,
    Today,
    ToolConfirmDrink,
    ToolConfirmMeal,
    ToolConfirmMed,
    ToolEndCheckin,
    ToolFlagConcern,
    ToolReportRefusal,
    ToolResult,
    Watch,
    WatchRequest,
    Week,
    WsCheckin,
)
from starlette.websockets import WebSocketDisconnect

from .auth import COOKIE, MAX_AGE, Auth, client_ip, is_https, require_internal, require_session, ws_allowed
from .checkins import CheckinService, Conflict, NotFound
from .clock import local_day_bounds
from .route import KioskHeartbeat
from .seed import seed

log = logging.getLogger("hearth.core")


def svc(request: Request) -> CheckinService:
    return request.app.state.svc


async def voice_available(request: Request) -> bool:
    from hearth.integrations.search import TypesenseSearch

    search = svc(request).search
    return isinstance(search, TypesenseSearch) and await search.voice_available()


hooks = APIRouter(prefix="/hooks")


@hooks.post("/chert/{key}")
async def chert_hook(key: str, request: Request) -> dict:
    expected = request.app.state.settings.chert_hook_key
    if not expected or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(status_code=404)
    try:
        event = await request.json()
    except ValueError:
        return {"action": "decline"}
    if not isinstance(event, dict) or event.get("type") != "call.incoming":
        return {"ok": True}
    data = event.get("data")
    call_id = data.get("call_id") if isinstance(data, dict) else None
    if not isinstance(call_id, str) or not call_id.strip():
        log.info("inbound FaceTime declined: missing call_id")
        return {"action": "decline"}
    return await svc(request).incoming(call_id)


# ============================================================================ public
public = APIRouter(prefix="/api")


@public.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(ok=True, transport_default=request.app.state.settings.transport_default,
                  integrations=request.app.state.health())


@public.post("/auth/login", response_model=Me)
async def login(body: LoginRequest, request: Request, response: Response) -> Me:
    auth: Auth = request.app.state.auth
    if auth.rate_limited(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many tries. Please wait a minute and try again.")
    if not auth.check_passcode(body.passcode):
        raise HTTPException(status_code=401, detail="That passcode didn't match.")
    response.set_cookie(COOKIE, auth.sign(), max_age=MAX_AGE, httponly=True, samesite="lax",
                        secure=is_https(request), path="/")
    r = await svc(request).resident()
    return Me(name=r.daughter_name, resident=r, voice=await voice_available(request),
              watch=request.app.state.settings.watch)


@public.post("/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=is_https(request))
    return resp


@public.get("/kiosk/token", response_model=KioskToken)
async def kiosk_token(request: Request, key: str = Query(...), device: str | None = None) -> KioskToken:
    auth: Auth = request.app.state.auth
    if not auth.check_internal(key):
        raise HTTPException(status_code=401, detail="That kiosk key isn't right.")
    calls = request.app.state.real_calls
    if calls is None:
        raise HTTPException(status_code=503, detail="Kiosk calls aren't set up on this server yet.")
    try:
        return await calls.kiosk_token(device or request.app.state.settings.kiosk_device)
    except Exception as e:
        log.warning("kiosk token failed: %s", e)
        # hearth.calls.CallsUnavailable carries a plain sentence meant for the page.
        unavailable = type(e).__name__ == "CallsUnavailable" and str(e)
        detail = str(e) if unavailable else "Kiosk calls aren't available right now."
        raise HTTPException(status_code=503, detail=detail) from e


# ============================================================================ session-only
api = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


@api.get("/me", response_model=Me)
async def me(request: Request) -> Me:
    r = await svc(request).resident()
    return Me(name=r.daughter_name, resident=r, voice=await voice_available(request),
              watch=request.app.state.settings.watch)


@api.get("/today", response_model=Today)
async def today(request: Request) -> Today:
    return await svc(request).today()


@api.get("/week", response_model=Week)
async def week(request: Request) -> Week:
    return await svc(request).week()


@api.get("/events", response_model=list[HearthEvent])
async def events(request: Request, kind: str | None = None, since: datetime | None = None,
                 limit: int = Query(50, ge=1, le=500)) -> list[HearthEvent]:
    return await svc(request).store.list_events(since=since, kind=kind or None, limit=limit)


@api.post("/events", response_model=HearthEvent, status_code=201)
async def add_event(body: ManualEventRequest, request: Request) -> HearthEvent:
    return await svc(request).add_manual_event(body)


@api.post("/checkins", response_model=Checkin, status_code=201)
async def start_checkin(body: StartCheckinRequest, request: Request) -> Checkin:
    try:
        return await svc(request).start(body, trigger="manual")
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@api.get("/checkins/{checkin_id}", response_model=Checkin)
async def get_checkin(checkin_id: str, request: Request) -> Checkin:
    c = await svc(request).store.get_checkin(checkin_id)
    if c is None:
        raise HTTPException(status_code=404, detail="That check-in doesn't exist.")
    return c


@api.post("/checkins/{checkin_id}/cancel", response_model=Checkin)
async def cancel_checkin(checkin_id: str, request: Request) -> Checkin:
    try:
        return await svc(request).cancel(checkin_id)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@api.get("/alerts", response_model=list[Alert])
async def alerts(request: Request, status: Literal["open", "handled", "all"] = "open") -> list[Alert]:
    return await svc(request).store.list_alerts(None if status == "all" else status)


@api.post("/alerts/{alert_id}/action", response_model=Alert)
async def alert_action(alert_id: str, body: AlertActionRequest, request: Request) -> Alert:
    try:
        return await svc(request).alert_action(alert_id, body)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@api.get("/schedule", response_model=list[ScheduleItem])
async def schedule(request: Request) -> list[ScheduleItem]:
    return await svc(request).store.list_schedule()


def _check_time(t: str) -> None:
    try:
        h, m = t.split(":")
        assert len(h) == 2 and len(m) == 2 and 0 <= int(h) < 24 and 0 <= int(m) < 60
    except (ValueError, AssertionError) as e:
        raise HTTPException(status_code=422, detail="Time must look like 14:30.") from e


@api.post("/schedule", response_model=ScheduleItem, status_code=201)
async def add_schedule(body: ScheduleItem, request: Request) -> ScheduleItem:
    _check_time(body.time)
    s = svc(request)
    r = await s.resident()
    item = body.model_copy(update={"id": body.id or f"{body.kind}-{secrets.token_hex(3)}", "resident_id": r.id})
    if await s.store.get_schedule_item(item.id):
        item = item.model_copy(update={"id": f"{item.id}-{secrets.token_hex(2)}"})
    await s.store.put_schedule_item(item)
    return item


@api.put("/schedule/{item_id}", response_model=ScheduleItem)
async def put_schedule(item_id: str, body: ScheduleItem, request: Request) -> ScheduleItem:
    _check_time(body.time)
    s = svc(request)
    if await s.store.get_schedule_item(item_id) is None:
        raise HTTPException(status_code=404, detail="That schedule item doesn't exist.")
    item = body.model_copy(update={"id": item_id})
    await s.store.put_schedule_item(item)
    return item


@api.delete("/schedule/{item_id}", status_code=204)
async def delete_schedule(item_id: str, request: Request) -> Response:
    if not await svc(request).store.delete_schedule_item(item_id):
        raise HTTPException(status_code=404, detail="That schedule item doesn't exist.")
    return Response(status_code=204)


@api.get("/cues", response_model=list[Cue])
async def cues(request: Request) -> list[Cue]:
    return await svc(request).cues()


@api.put("/cues/{cue_id}", response_model=Cue)
async def put_cue(cue_id: str, body: Cue, request: Request) -> Cue:
    s = svc(request)
    cue = body.model_copy(update={"id": cue_id, "slot_label": None})
    await s.store.put_cue(cue)
    return s.with_slot(cue, await s.resident())


PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PHOTO_MAX = 5 * 1024 * 1024


@api.post("/cues/{cue_id}/photo", response_model=Cue)
async def cue_photo(cue_id: str, request: Request, file: Annotated[UploadFile, File()]) -> Cue:
    s = svc(request)
    cue = await s.store.get_cue(cue_id)
    if cue is None:
        raise HTTPException(status_code=404, detail="That cue doesn't exist.")
    ext = PHOTO_TYPES.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(status_code=415, detail="Please choose a JPEG, PNG or WebP photo.")
    data = await file.read(PHOTO_MAX + 1)
    if len(data) > PHOTO_MAX:
        raise HTTPException(status_code=413, detail="That photo is over 5 MB. Please choose a smaller one.")
    magic_ok = (data[:3] == b"\xff\xd8\xff" if ext == "jpg" else data[:8] == b"\x89PNG\r\n\x1a\n" if ext == "png"
                else data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    if not magic_ok:
        raise HTTPException(status_code=415, detail="That file doesn't look like a photo.")
    media: Path = request.app.state.settings.media_dir / "cues"
    media.mkdir(parents=True, exist_ok=True)
    name = f"{cue_id}-{secrets.token_hex(4)}.{ext}"
    (media / name).write_bytes(data)
    if cue.image_url and cue.image_url.startswith("/media/cues/"):
        old = media / Path(cue.image_url).name
        if old.exists():
            old.unlink()
    cue = cue.model_copy(update={"image_url": f"/media/cues/{name}"})
    await s.store.put_cue(cue)
    return s.with_slot(cue, await s.resident())


@api.get("/resident", response_model=Resident)
async def get_resident(request: Request) -> Resident:
    return await svc(request).resident()


@api.put("/resident", response_model=Resident)
async def put_resident(body: Resident, request: Request) -> Resident:
    s = svc(request)
    r = await s.resident()
    body = body.model_copy(update={"id": r.id})
    await s.store.put_resident(body)
    return body


@api.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    s = svc(request)
    q = body.q.strip()
    if body.voice_wav_b64 is not None:
        from hearth.integrations.search import TypesenseSearch

        if not isinstance(s.search, TypesenseSearch) or not s.search.voice_enabled:
            raise HTTPException(status_code=400, detail="Voice questions need search to be set up.")
        try:
            q = await s.search.transcribe(body.voice_wav_b64)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    elif len(q) < 2:
        raise HTTPException(status_code=400, detail="Please write a question between 2 and 300 characters.")
    now = s.clock.now()
    recent = await s.store.list_events(since=now - timedelta(days=8), limit=500)
    try:
        return await s.search.ask(q, now, recent)
    except Exception as e:  # noqa: BLE001
        log.warning("search failed (%s); using keyword fallback", e)
        return await request.app.state.fallback_search.ask(q, now, recent)


@api.get("/digest", response_model=Digest)
async def digest(request: Request, day: str | None = None) -> Digest:
    s = svc(request)
    r = await s.resident()
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date() if day else s.clock.now().astimezone(
            ZoneInfo(r.timezone)).date()
    except ValueError as e:
        raise HTTPException(status_code=422, detail="Day must look like 2026-11-14.") from e
    start, end = local_day_bounds(d, r.timezone)
    evs = await s.store.list_events(since=start, until=end, limit=500)
    try:
        return await s.digest.write(d.isoformat(), evs, r.family_name_for_her)
    except Exception as e:  # noqa: BLE001
        log.warning("digest failed (%s); using the template", e)
        return await request.app.state.fallback_digest.write(d.isoformat(), evs, r.family_name_for_her)


RETENTION_DAYS = 90


@api.get("/privacy", response_model=Privacy)
async def privacy(request: Request) -> Privacy:
    s = svc(request)
    r = await s.resident()
    cue_photos = sum(1 for c in await s.store.list_cues() if c.image_url)
    return Privacy(
        stored=[
            StoredThing(what="Check-in events", kept=True,
                        detail=f"Time, kind and a short description. Deleted after {RETENTION_DAYS} days."),
            StoredThing(what="Schedule", kept=True, detail="The check-in times you set."),
            StoredThing(what="Cue photos", kept=True,
                        detail="Only the photos you upload of her mug, plate and pill box."),
            StoredThing(what="Consent record", kept=True, detail="Who agreed to the check-ins, and when."),
        ],
        never_stored=["Video or audio of any call", "Images from her camera", "Transcripts of what was said",
                      "Voiceprints"],
        retention_days_events=RETENTION_DAYS,
        in_transit=["Chert (FaceTime bridge)", "LiveKit Cloud", "Google Gemini"] + ([
            ("While watch mode is on, one still image about every 10 seconds goes to Google's Gemini "
            "to check she is OK. Images are never stored.")] if s.settings.watch else []),
        consent=r.consent,
        counts={"events": await s.store.count_events(), "checkins": len(await s.store.list_checkins(10000)),
                "alerts": len(await s.store.list_alerts()), "cue_photos": cue_photos,
                "frames_stored": 0, "recordings": 0, "transcripts": 0},
    )


@api.delete("/privacy/data")
async def delete_data(request: Request) -> dict[str, int]:
    s = svc(request)
    if await s.store.active_checkin() is not None:
        raise HTTPException(status_code=409, detail="A check-in is running. Please try again when it ends.")
    n = await s.store.delete_all_events()
    await s.store.delete_checkins_and_alerts()
    await s.search.clear()
    log.info("privacy: deleted %d events and all check-ins and alerts", n)
    return {"deleted_events": n}


@api.get("/demo/clock", response_model=Clock)
async def get_clock(request: Request) -> Clock:
    return svc(request).clock.model()


@api.post("/demo/clock", response_model=Clock)
async def set_clock(body: ClockRequest, request: Request) -> Clock:
    s = svc(request)
    given = sum([body.advance_minutes is not None, body.set_time is not None, bool(body.reset)])
    if given != 1:
        raise HTTPException(status_code=422, detail="Send exactly one of advance_minutes, set_time or reset.")
    if body.reset:
        await s.clock.set_offset(0)
    elif body.advance_minutes is not None:
        await s.clock.set_offset(s.clock.offset_minutes + body.advance_minutes)
    else:
        try:
            h, m = (int(x) for x in (body.set_time or "").split(":"))
            r = await s.resident()
            z = ZoneInfo(r.timezone)
            local = s.clock.now().astimezone(z)
            target = local.replace(hour=h, minute=m, second=0, microsecond=0)
        except (ValueError, NotFound) as e:
            raise HTTPException(status_code=422, detail="Time must look like 14:00.") from e
        delta = (target - local).total_seconds() / 60
        await s.clock.set_offset(s.clock.offset_minutes + round(delta))
    await s.bus.publish(s.clock.ws())
    if request.app.state.settings.scheduler:
        s._bg(s.tick())
    return s.clock.model()


@api.post("/demo/reset", response_model=Today)
async def demo_reset(request: Request) -> Today:
    s = svc(request)
    active = await s.store.active_checkin()
    if active is not None:
        await s.cancel(active.id)
    n = await seed(s.store, s.clock.now())
    s.reset_tick()
    s._bg(s.reindex())
    log.info("demo reset: seeded %d events", n)
    return await s.today()


@api.post("/push/subscribe", status_code=204)
async def push_subscribe(body: PushSubscription, request: Request) -> Response:
    await svc(request).store.add_push_subscription(body)
    return Response(status_code=204)


@api.get("/push/key")
async def push_key(request: Request) -> dict[str, str | None]:
    return {"public_key": request.app.state.settings.vapid_public_key}


@api.get("/ws-ticket")
async def ws_ticket(request: Request) -> dict[str, str]:
    return {"ticket": request.app.state.auth.new_ticket()}


# ============================================================================ internal (agent)
internal = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])


async def _tool(coro) -> ToolResult | JSONResponse:
    try:
        return await coro
    except NotFound as e:
        return JSONResponse(status_code=404, content={"detail": str(e)})


@internal.get("/checkins/{checkin_id}", response_model=Checkin)
async def i_get(checkin_id: str, request: Request) -> Checkin:
    c = await svc(request).store.get_checkin(checkin_id)
    if c is None:
        raise HTTPException(status_code=404, detail="That check-in doesn't exist.")
    return c


@internal.post("/checkins/{checkin_id}/status", response_model=ToolResult)
async def i_status(checkin_id: str, body: AgentStatus, request: Request):
    return await _tool(svc(request).agent_status(checkin_id, body))


@internal.post("/checkins/{checkin_id}/caption", status_code=204)
async def i_caption(checkin_id: str, body: AgentCaption, request: Request) -> Response:
    await svc(request).caption(checkin_id, body)
    return Response(status_code=204)


@internal.post("/checkins/{checkin_id}/confirm_drink", response_model=ToolResult)
async def i_drink(checkin_id: str, body: ToolConfirmDrink, request: Request):
    return await _tool(svc(request).confirm_drink(checkin_id, body))


@internal.post("/checkins/{checkin_id}/confirm_meal", response_model=ToolResult)
async def i_meal(checkin_id: str, body: ToolConfirmMeal, request: Request):
    return await _tool(svc(request).confirm_meal(checkin_id, body))


@internal.post("/checkins/{checkin_id}/confirm_med", response_model=ToolResult)
async def i_med(checkin_id: str, body: ToolConfirmMed, request: Request):
    return await _tool(svc(request).confirm_med(checkin_id, body))


@internal.post("/checkins/{checkin_id}/report_refusal", response_model=ToolResult)
async def i_refusal(checkin_id: str, body: ToolReportRefusal, request: Request):
    return await _tool(svc(request).report_refusal(checkin_id, body))


@internal.post("/checkins/{checkin_id}/flag_concern", response_model=ToolResult)
async def i_concern(checkin_id: str, body: ToolFlagConcern, request: Request):
    return await _tool(svc(request).flag_concern(checkin_id, body))


@internal.post("/checkins/{checkin_id}/end", response_model=ToolResult)
async def i_end(checkin_id: str, body: ToolEndCheckin, request: Request):
    return await _tool(svc(request).end_checkin(checkin_id, body))


# ============================================================================ live
live = APIRouter()


@live.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    s: CheckinService = ws.app.state.svc
    q = s.bus.subscribe()
    try:
        active = await s.store.active_checkin()
        if active is not None:
            await ws.send_text(WsCheckin(checkin=active).model_dump_json())
        await ws.send_text(s.clock.ws().model_dump_json())

        async def pump() -> None:
            while True:
                await ws.send_text(await q.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                await ws.receive_text()  # clients don't send; this notices disconnects
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
    finally:
        s.bus.unsubscribe(q)


def require_watch_enabled(request: Request) -> None:
    if not request.app.state.settings.watch:
        raise HTTPException(404, "Watch mode is not enabled.")


# Watch uses precisely the kiosk token query authentication, not the agent header.
def require_watch_key(request: Request, key: str = Query("")) -> None:
    require_watch_enabled(request)
    require_kiosk_key(request, key)


def require_kiosk_key(request: Request, key: str = Query("")) -> None:
    if not request.app.state.auth.check_internal(key):
        raise HTTPException(401, "That kiosk key isn't right.")


watch_internal = APIRouter(prefix="/internal/watch", dependencies=[Depends(require_watch_key)])
kiosk_internal = APIRouter(prefix="/internal/kiosk", dependencies=[Depends(require_kiosk_key)])


@kiosk_internal.post("/heartbeat", status_code=204)
async def kiosk_heartbeat(body: KioskHeartbeat, request: Request) -> Response:
    svc(request).route.heartbeat(body)
    return Response(status_code=204)


@api.get("/route", response_model=Route)
async def route_decision(request: Request, response: Response) -> Route:
    response.headers["Cache-Control"] = "no-store"
    return svc(request).route.decide()


@api.get("/watch", response_model=Watch)
async def watch_state(request: Request) -> Watch:
    return request.app.state.watch.view()


@api.post("/watch", response_model=Watch, dependencies=[Depends(require_watch_enabled)])
async def start_watch(body: WatchRequest, request: Request) -> Watch:
    return request.app.state.watch.start(body.minutes)


@api.delete("/watch", response_model=Watch, dependencies=[Depends(require_watch_enabled)])
async def stop_watch(request: Request) -> Watch:
    return request.app.state.watch.stop()


@watch_internal.get("/state")
async def kiosk_watch_state(request: Request) -> dict[str, bool]:
    watch = request.app.state.watch
    return {"active": watch.view().active and await svc(request).store.active_checkin() is None}


@watch_internal.post("/frame", response_model=Watch)
async def watch_frame(request: Request, response: Response) -> Watch:
    response.headers["Cache-Control"] = "no-store"
    watch = request.app.state.watch
    await watch.require_active()
    if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
        raise HTTPException(415, "Send one JPEG image.")
    # Stream with a hard cap, including requests without Content-Length.
    jpeg = bytearray()
    async for chunk in request.stream():
        if len(jpeg) + len(chunk) > 300 * 1024:
            raise HTTPException(413, "Choose an image under 300 KB.")
        jpeg.extend(chunk)
    if not jpeg.startswith(b"\xff\xd8\xff") or not jpeg.endswith(b"\xff\xd9"):
        raise HTTPException(400, "Send one JPEG image.")
    try:
        return await watch.frame(bytes(jpeg))
    finally:
        jpeg.clear()
