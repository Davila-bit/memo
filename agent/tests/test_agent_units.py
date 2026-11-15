"""Unit tests that need no keys and no network beyond an in-process ASGI stub."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"))

from hearth_contracts import ToolConfirmDrink, ToolFlagConcern, ToolReportRefusal, ToolResult  # noqa: E402

from hearth_agent.api import HearthApi  # noqa: E402
from hearth_agent.budget import BudgetTimer  # noqa: E402
from hearth_agent.cuecard import CardState, CueCard, H, W, render  # noqa: E402
from hearth_agent.prompts import instructions, no_video_instructions, opening_line  # noqa: E402
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


@pytest.mark.parametrize("kind", ["drink", "meal", "med"])
def test_inbound_prompt_only_changes_the_opening(kind: str) -> None:
    outbound = make_job("c1", kind, "http://x", 90, "chert-bridge-")
    assert not outbound.inbound
    inbound = outbound.model_copy(update={"inbound": True})
    assert opening_line(inbound) == "Hello Margaret, it's Sarah's helper. Thank you for calling."
    assert instructions(inbound) == instructions(outbound).replace(
        opening_line(outbound), opening_line(inbound))


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
    low = await a.confirm_drink(ToolConfirmDrink(amount="sip", confidence=0.3, evidence="maybe drinking"))
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


async def test_tool_client_report_reply_and_end(client: httpx.AsyncClient) -> None:
    a = HearthApi("http://stub", "c_report", token="dev-internal", client=client)
    c = _call(None)
    confidence, evidence = c.clamp_unseen(0.9, "Said: I've had my water")
    result = await a.confirm_drink(ToolConfirmDrink(amount="some", confidence=confidence, evidence=evidence))
    text = await c.confirmed(result, confidence)
    assert text == "Thank her and say goodbye. Then call end_checkin('done')."
    assert (await a.end("done")).ok
    checkin = await a.get_checkin()
    assert checkin and checkin.status == "ended" and checkin.outcome == "reported"


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


async def test_budget_expire_callback_can_end_the_call() -> None:
    """Regression: finish() cancels the budget; when called FROM the budget it must not kill itself."""
    done: list[str] = []
    t: BudgetTimer

    async def warn() -> None:
        pass

    async def expire() -> None:
        t.cancel()  # what Call.finish does
        await asyncio.sleep(0.05)  # e.g. the POST /end
        done.append("end posted")

    t = BudgetTimer(0.1, warn, expire, warn_before=0.05)
    t.start()
    await asyncio.sleep(0.3)
    assert done == ["end posted"]


async def test_budget_timer_cancel() -> None:
    hit: list[str] = []

    async def cb() -> None:
        hit.append("x")

    t = BudgetTimer(0.2, cb, cb, warn_before=0.1)
    t.start()
    t.cancel()
    await asyncio.sleep(0.3)
    assert hit == []


# ------------------------------------------------------------------ no-video / still-picture gate


class _FakeVideo:
    def __init__(self, seeing: bool, motion: float | None) -> None:
        self._seeing, self._motion = seeing, motion

    def seeing(self, within: float = 4.0) -> bool:
        return self._seeing

    def max_motion(self, window: float = 25.0) -> float | None:
        return self._motion


def _call(video) -> "Call":  # noqa: F821
    from hearth_agent.worker import Call

    job = make_job("c1", "drink", "http://x", 90, "kiosk-")
    c = Call(ctx=None, job=job, api=None, card=CueCard(CardState()))  # type: ignore[arg-type]
    c.video = video
    return c


@pytest.mark.parametrize(
    "video,ok",
    [
        (None, False),  # no video track at all
        (_FakeVideo(False, None), False),  # track gone / muted
        (_FakeVideo(True, 0.0), False),  # a still photo: nothing moved
        (_FakeVideo(True, 0.4), False),  # encoder noise only
        (_FakeVideo(True, 6.0), True),  # a real movement
    ],
)
def test_visual_confirmation_needs_moving_video(video, ok: bool) -> None:
    c = _call(video)
    conf, ev = c.clamp_unseen(0.95, "held up blue mug, two drinks")
    if ok:
        assert conf == 0.95 and ev == "held up blue mug, two drinks" and c.clamped is None
    else:
        assert conf <= 0.4 and ev.startswith("Said:") and c.clamped in ("still", "no_video")


@pytest.mark.parametrize("kind,task", [("drink", "have her drink"), ("meal", "have her meal"),
                                      ("med", "take her tablets")])
async def test_no_video_check_delivers_explicit_report_instructions(monkeypatch, kind, task) -> None:
    import hearth_agent.worker as worker

    monkeypatch.setattr(worker, "NO_VIDEO_AFTER", 0)  # don't wait or start a model session
    c = _call(None)
    c.job = c.job.model_copy(update={"kind": kind})
    c.session = _FakeSession()
    await c._video_check()
    assert c.session.said == [no_video_instructions(c.job)]
    text = c.session.said[0]
    assert 'Say once: "I can\'t see you just now, but I can hear you."' in text
    assert task in text and "tell you when she has" in text and "wait for her answer" in text
    assert f"confirm_{kind}(" in text and "confidence=0.4" in text
    assert "`Said:` followed by a short gist of her words" in text
    assert 'On the tool reply, thank her, say goodbye and call end_checkin("done")' in text
    assert "report_refusal" in text and "never give medical advice or change doses" in text
    if kind == "med":
        assert "steps_seen=[]" in text


async def test_video_check_leaves_visual_task_alone(monkeypatch) -> None:
    import hearth_agent.worker as worker

    monkeypatch.setattr(worker, "NO_VIDEO_AFTER", 0)
    c = _call(_FakeVideo(True, 6))
    c.video.frames_sent = 10
    c.session = _FakeSession()
    await c._video_check()
    assert c.session.said == []


@pytest.mark.parametrize("confidence,evidence,expected", [
    (0.9, "I've had my water", "Said: I've had my water"),
    (0.4, "Said: I've eaten", "Said: I've eaten"),
    (0.2, "said: I've taken my tablets", "Said: I've taken my tablets"),
])
async def test_no_video_cap_and_saved_report_reply(confidence, evidence, expected) -> None:
    c = _call(None)
    conf, ev = c.clamp_unseen(confidence, evidence)
    assert conf == min(confidence, 0.4) and ev == expected and c.clamped == "no_video"
    text = await c.confirmed(ToolResult(ok=True, event_id="ev_report", say="Thank her and say goodbye."), conf)
    assert c.outcome == "reported" and c.card.state.mode == "thanks"
    assert text == "Thank her and say goodbye. Then call end_checkin('done')."


async def test_no_video_unsaved_report_does_not_claim_saved() -> None:
    c = _call(None)
    conf, _ = c.clamp_unseen(0.9, "Said: I've had water")
    text = await c.confirmed(None, conf)
    assert c.outcome is None and "could not be saved" in text and "end_checkin('done')" in text


# ------------------------------------------------------------------ captions: sentence start + paging


def test_caption_shows_current_sentence_from_its_start() -> None:
    from hearth_agent.cuecard import caption_page
    from hearth_agent.worker import current_sentence

    spoken = "Hello Margaret, it's Sarah's helper. Could you please hold up your blue mug so I can see it?"
    cur = current_sentence(spoken)
    assert cur == "Could you please hold up your blue mug so I can see it?"
    lines = caption_page(cur)
    assert 1 <= len(lines) <= 2 and lines[0].startswith("Could you please")
    assert " ".join(lines) == cur  # nothing cut off
    # mid-sentence (words still arriving) also starts at the beginning of the sentence
    assert caption_page(current_sentence("Hello Margaret, it's Sarah's helper. Could you please"))[0] == "Could you please"
    img = render(CardState(mode="asking", line="Time for some water, Margaret", caption=cur))
    assert img.size == (W, H)


def test_caption_pages_long_sentence() -> None:
    from hearth_agent.cuecard import caption_page

    words = ("When you are ready, Margaret, could you please pick up your blue mug from the table and hold "
             "it up high so that I can see it clearly on my screen here, thank you very much")
    full = caption_page(words, lines_per_page=99)
    assert len(full) >= 3
    first = caption_page(" ".join(full[:2]))
    assert first == full[:2]  # page 1 = lines 1-2
    assert caption_page(" ".join(full[:3])) == [full[2]]  # line 3 arrives: page 2 starts at line 3
    assert caption_page(" ".join(full[:4])) == full[2:4]  # not a rolling tail (which would be 2-3)


# ------------------------------------------------------------------ nobody there = no answer


class _FakeSession:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.agent_state = "listening"

    def generate_reply(self, instructions: str = "") -> None:
        self.said.append(instructions)


def _watch_call(ask: float, end: float):
    c = _call(_FakeVideo(True, 0.0))
    c.session = _FakeSession()
    c.no_answer_ask_s, c.no_answer_end_s = ask, end
    ended: list[str] = []

    async def fake_finish(reason: str, detail: str = "", wait_speech: bool = True) -> None:
        ended.append(reason)
        c.ending = True

    c.finish = fake_finish  # type: ignore[method-assign]
    return c, ended


async def test_no_answer_asks_then_ends() -> None:
    c, ended = _watch_call(0.05, 0.12)
    t = asyncio.create_task(c.no_answer_watch())
    await asyncio.sleep(0.08)
    assert len(c.session.said) == 1 and "Margaret, are you there?" in c.session.said[0]
    await t
    assert ended == ["no_answer"] and "try again a little later" in c.session.said[1]
    assert c.card.state.mode == "okay"


async def test_no_answer_stops_when_she_speaks() -> None:
    c, ended = _watch_call(0.05, 0.12)
    c.live.set()
    t = asyncio.create_task(c.no_answer_watch())
    await asyncio.sleep(0.08)
    c.heard_her = True  # she answered "yes, I'm here"
    await t
    assert ended == [] and len(c.session.said) == 1


async def test_no_answer_never_fires_after_a_tool() -> None:
    c, ended = _watch_call(0.03, 0.06)
    c.last_tool_at = 1.0  # e.g. the model reported a refusal
    await c.no_answer_watch()
    assert ended == [] and c.session.said == []


def test_hung_up_reason_flag(monkeypatch) -> None:
    from hearth_agent.worker import hung_up_reason

    monkeypatch.delenv("HEARTH_REASON_HUNG_UP", raising=False)
    assert hung_up_reason() == "error"
    monkeypatch.setenv("HEARTH_REASON_HUNG_UP", "1")
    assert hung_up_reason() == "hung_up"


# ------------------------------------------------------------------ clean exit: one end only


async def test_end_not_resent_after_cancel(client: httpx.AsyncClient) -> None:
    """The core hangs up on the first `end`, which can cancel us mid-request: never resend on job close."""
    a = HearthApi("http://stub", "c7", token="dev-internal", client=client)
    orig = a._post

    async def slow_post(path, body):
        await asyncio.sleep(1)
        return await orig(path, body)

    a._post = slow_post  # type: ignore[method-assign]
    t = asyncio.create_task(a.end("time_budget"))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert a.ended and await a.end("error") is None  # the shutdown hook sends nothing


async def test_hung_up_body_sent_when_flagged(client: httpx.AsyncClient) -> None:
    a = HearthApi("http://stub", "c8", token="dev-internal", client=client)
    # the stub validates against today's contract, so `hung_up` is rejected there (422) -> logged, None
    assert await a.end("hung_up") is None and not a.ended
