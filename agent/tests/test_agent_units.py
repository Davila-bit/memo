"""Unit tests that need no keys and no network beyond an in-process ASGI stub."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"))

from hearth_contracts import ToolConfirmDrink, ToolFlagConcern, ToolReportRefusal  # noqa: E402

from hearth_agent.api import HearthApi  # noqa: E402
from hearth_agent.budget import BudgetTimer  # noqa: E402
from hearth_agent.cuecard import CardState, CueCard, H, W, render  # noqa: E402
from hearth_agent.prompts import instructions, opening_line  # noqa: E402
from hearth_agent.safety import match_concern  # noqa: E402
from hearth_agent.worker import is_live, parse_job  # noqa: E402

import stub_api  # noqa: E402
from dispatch import make_job  # noqa: E402

# ------------------------------------------------------------------ job parsing


def test_parse_job_roundtrip() -> None:
    job = make_job("c1", "med", "http://x", 90, "kiosk-")
    parsed = parse_job(job.model_dump_json())
    assert parsed.checkin_id == "c1" and parsed.cue and parsed.cue.slot_label == "TUE · MORNING"


@pytest.mark.parametrize("bad", [None, "", "{}", "not json", '{"checkin_id": "x"}'])
def test_parse_job_rejects(bad) -> None:
    with pytest.raises(ValueError):
        parse_job(bad)


def test_is_live() -> None:
    assert is_live('{"hearth_live": true, "checkin_id": "a"}', "a")
    assert is_live('{"hearth_live": true}', "a")
    assert not is_live('{"hearth_live": true, "checkin_id": "b"}', "a")
    assert not is_live('{"hearth_live": "yes"}', "a")
    assert not is_live("", "a") and not is_live("garbage", "a")


def test_prompt_rules() -> None:
    job = make_job("c1", "drink", "http://x", 90, "kiosk-")
    text = instructions(job)
    assert opening_line(job) == "Hello Margaret, it's Sarah's helper."
    assert "never pretend" in text and "swallowed" in text and "911" not in text
    assert "I'm going to get Sarah for you right now. Stay where you are." in text


# ------------------------------------------------------------------ keyword backup


@pytest.mark.parametrize(
    "said,cat",
    [
        ("Help me, I fell and I can't get up", "said_fell_or_hurt"),
        ("I've fallen in the hall", "said_fell_or_hurt"),
        ("please help", "asked_for_help"),
        ("my chest feels tight", "reports_pain_or_unwell"),
        ("I can’t breathe properly", "reports_pain_or_unwell"),
        ("I feel a bit dizzy", "reports_pain_or_unwell"),
        ("my arm is numb", "reports_pain_or_unwell"),
        ("my knee hurts", "said_fell_or_hurt"),
    ],
)
def test_keyword_hits(said: str, cat: str) -> None:
    assert match_concern(said) == cat


@pytest.mark.parametrize("said", ["No thank you, I'm not thirsty", "I had tea earlier", "Here's my cup", "helpful"])
def test_keyword_misses(said: str) -> None:
    assert match_concern(said) is None


# ------------------------------------------------------------------ tool client against the stub


@pytest.fixture
def client() -> httpx.AsyncClient:
    stub_api.LOG.clear()
    stub_api.CHECKINS.clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=stub_api.app), base_url="http://stub")


async def test_tool_client_flow(client: httpx.AsyncClient) -> None:
    a = HearthApi("http://stub", "c9", token="dev-internal", client=client)
    assert (await a.status("connected")).ok
    await a.caption("helper", "Hello Margaret")
    low = await a.confirm_drink(ToolConfirmDrink(amount="sip", confidence=0.3, evidence="Said: drinking"))
    assert low and low.say  # low confidence -> stored as note, agent asked to check again
    ok = await a.confirm_drink(ToolConfirmDrink(amount="most", confidence=0.9, evidence="held mug, drank"))
    assert ok and ok.ok and not ok.say
    assert (await a.report_refusal(ToolReportRefusal(said="not thirsty", asked_times=2))).ok
    assert (await a.flag_concern(ToolFlagConcern(category="asked_for_help", evidence_quote="help"))).ok
    assert (await a.end("done")).ok
    assert await a.end("done") is None  # only the first end is sent
    c = await a.get_checkin()
    assert c and c.status == "ended" and c.outcome == "confirmed"
    calls = [e["call"] for e in stub_api.LOG]
    assert calls == ["status", "caption", "confirm_drink", "confirm_drink", "report_refusal", "flag_concern", "end"]
    assert all("text" not in e and "evidence" not in e for e in stub_api.LOG)  # sizes only


async def test_tool_client_bad_token_degrades(client: httpx.AsyncClient) -> None:
    a = HearthApi("http://stub", "c9", token="wrong", client=client)
    assert await a.status("connected") is None  # logged, not raised


async def test_tool_client_unreachable() -> None:
    a = HearthApi("http://127.0.0.1:9", "c9", token="x", timeout=0.3)
    assert await a.end("error") is None
    await a.aclose()


# ------------------------------------------------------------------ cue card


@pytest.mark.parametrize("mode", ["waiting", "asking", "watching", "thanks", "okay", "concern"])
@pytest.mark.parametrize("kind", ["drink", "meal", "med"])
def test_cue_card_renders(mode: str, kind: str) -> None:
    st = CardState(mode=mode, kind=kind, line="Time for some water, Margaret", caption="Hello Margaret",
                   slot_label="TUE · MORNING" if kind == "med" else None)
    img = render(st)
    assert img.size == (W, H)
    # content is inside the central 4:3 area: the middle band is not blank paper
    mid = img.crop((160, 80, 1120, 560))
    assert len(set(mid.getdata())) > 20
    # caption strip has text pixels
    strip = img.crop((160, 590, 1120, 715))
    assert len(set(strip.getdata())) > 5
    # outer side bands (cropped by FaceTime fill) carry nothing but background
    left = img.crop((0, 80, 150, 580))
    assert len(set(left.getdata())) <= 2


def test_cue_card_redraws_only_on_change() -> None:
    card = CueCard(CardState(mode="waiting"))
    a = card.frame_rgba()
    assert card.frame_rgba() is a  # cached
    card.update(mode="waiting")
    assert card.frame_rgba() is a  # no change, no redraw
    card.update(mode="thanks")
    b = card.frame_rgba()
    assert b is not a and len(b) == W * H * 4


# ------------------------------------------------------------------ budget


async def test_budget_timer_warns_then_expires() -> None:
    events: list[str] = []

    async def warn() -> None:
        events.append("warn")

    async def expire() -> None:
        events.append("expire")

    t = BudgetTimer(0.3, warn, expire, warn_before=0.1)
    t.start()
    await asyncio.sleep(0.25)
    assert events == ["warn"]
    await asyncio.sleep(0.15)
    assert events == ["warn", "expire"] and t.expired


async def test_budget_timer_cancel() -> None:
    hit: list[str] = []

    async def cb() -> None:
        hit.append("x")

    t = BudgetTimer(0.2, cb, cb, warn_before=0.1)
    t.start()
    t.cancel()
    await asyncio.sleep(0.3)
    assert hit == []
