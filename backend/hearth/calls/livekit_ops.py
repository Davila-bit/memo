"""Small interface over the LiveKit server API, so the call state machine can be tested with a fake."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol

log = logging.getLogger("hearth.calls")

TrackKind = Literal["audio", "video", "data"]


@dataclass
class TrackState:
    kind: TrackKind
    muted: bool = False
    name: str = ""


@dataclass
class ParticipantState:
    identity: str
    is_agent: bool = False
    tracks: list[TrackState] = field(default_factory=list)

    def audio(self, *, unmuted_only: bool = False) -> list[TrackState]:
        return [t for t in self.tracks if t.kind == "audio" and (not unmuted_only or not t.muted)]

    def video(self) -> list[TrackState]:
        return [t for t in self.tracks if t.kind == "video"]


class RoomOps(Protocol):
    async def ensure_room(self, room: str, *, empty_timeout: int, max_participants: int) -> None: ...

    async def delete_room(self, room: str) -> None: ...

    async def dispatch_agent(self, room: str, agent_name: str, metadata: str) -> str:
        """Explicit dispatch; returns the dispatch id."""
        ...

    async def delete_dispatch(self, dispatch_id: str, room: str) -> None: ...

    async def participants(self, room: str) -> list[ParticipantState]: ...

    async def set_metadata(self, room: str, metadata: str) -> None: ...

    async def remove_participant(self, room: str, identity: str) -> None: ...

    async def aclose(self) -> None: ...


def mint_token(
    api_key: str,
    api_secret: str,
    *,
    identity: str,
    room: str,
    ttl_seconds: int,
    name: str | None = None,
) -> str:
    """Room-scoped join token (roomJoin + canPublish + canSubscribe for exactly `room`).

    livekit-api 2.0 `AccessToken.to_jwt` sets integer `nbf` = now and `exp` = now + ttl (calendar.timegm),
    `sub` = identity, and lowerCamel `video` grants: what Chert's bridge-token check expects.
    """
    from livekit import api

    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(name or identity)
        .with_ttl(datetime.timedelta(seconds=ttl_seconds))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


class LiveKitRoomOps:
    """Real implementation on livekit-api (Twirp over aiohttp). The client is created lazily inside the
    running event loop, because aiohttp sessions bind to a loop."""

    def __init__(self, url: str, api_key: str, api_secret: str, http_timeout: float = 10.0) -> None:
        self._url, self._key, self._secret = url, api_key, api_secret
        self._timeout = http_timeout
        self._api = None

    def _lk(self):
        if self._api is None:
            import aiohttp
            from livekit import api

            self._api = api.LiveKitAPI(
                self._url, self._key, self._secret, timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._api

    async def ensure_room(self, room: str, *, empty_timeout: int, max_participants: int) -> None:
        from livekit import api

        await self._lk().room.create_room(
            api.CreateRoomRequest(name=room, empty_timeout=empty_timeout, max_participants=max_participants)
        )

    async def delete_room(self, room: str) -> None:
        from livekit import api

        await self._lk().room.delete_room(api.DeleteRoomRequest(room=room))

    async def dispatch_agent(self, room: str, agent_name: str, metadata: str) -> str:
        from livekit import api

        d = await self._lk().agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name, room=room, metadata=metadata)
        )
        return d.id

    async def delete_dispatch(self, dispatch_id: str, room: str) -> None:
        await self._lk().agent_dispatch.delete_dispatch(dispatch_id, room)

    async def participants(self, room: str) -> list[ParticipantState]:
        from livekit import api
        from livekit.protocol import models

        try:
            res = await self._lk().room.list_participants(api.ListParticipantsRequest(room=room))
        except api.TwirpError as e:
            if e.code == "not_found":  # room not created yet / already gone
                return []
            raise
        out: list[ParticipantState] = []
        for p in res.participants:
            tracks = []
            for t in p.tracks:
                kind: TrackKind = (
                    "audio" if t.type == models.TrackType.AUDIO
                    else "video" if t.type == models.TrackType.VIDEO else "data"
                )
                tracks.append(TrackState(kind=kind, muted=bool(t.muted), name=t.name))
            out.append(
                ParticipantState(
                    identity=p.identity,
                    is_agent=p.kind == models.ParticipantInfo.Kind.AGENT,
                    tracks=tracks,
                )
            )
        return out

    async def set_metadata(self, room: str, metadata: str) -> None:
        from livekit import api

        await self._lk().room.update_room_metadata(api.UpdateRoomMetadataRequest(room=room, metadata=metadata))

    async def remove_participant(self, room: str, identity: str) -> None:
        from livekit import api

        await self._lk().room.remove_participant(api.RoomParticipantIdentity(room=room, identity=identity))

    async def aclose(self) -> None:
        if self._api is not None:
            await self._api.aclose()
            self._api = None
