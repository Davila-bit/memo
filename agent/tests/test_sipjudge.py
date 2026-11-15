"""The frame check: ring buffer, movement trigger and how its verdict adjusts the live model's confidence."""

from __future__ import annotations

import asyncio

from PIL import Image

from hearth_agent import sipjudge
from hearth_agent.sipjudge import FrameRing, Verdict, combine


def _img(level: int) -> Image.Image:
    return Image.new("RGB", (640, 360), (level, level, level))


def test_ring_keeps_only_recent_small_frames() -> None:
    r = FrameRing(fps=4, seconds=2, side=200)
    t = 100.0
    for i in range(20):
        if r.due(t):
            r.add_image(_img(i * 10), t)
        t += 0.25
    assert all(r.frames[-1][0] - f[0] <= 2.0 for f in r.frames)
    assert 7 <= len(r.frames) <= 9
    assert all(f[1][:2] == b"\xff\xd8" for f in r.frames)  # JPEG, in memory
    assert len(r.window(max_frames=4)) == 4


def test_settled_after_movement_fires_once_per_movement() -> None:
    r = FrameRing(fps=4, seconds=10)
    t, fired = 0.0, []
    levels = [100] * 8 + [180, 60, 200, 70] + [100] * 8 + [100] * 8
    for lv in levels:
        r.add_image(_img(lv), t)
        if r.settled_after_movement(t):
            fired.append(t)
        t += 0.25
    assert len(fired) == 1
    assert fired[0] > 3.0  # after the movement, once calm


def test_still_picture_never_fires() -> None:
    r = FrameRing(fps=4, seconds=10)
    t = 0.0
    for _ in range(40):
        r.add_image(_img(120), t)
        assert not r.settled_after_movement(t)
        t += 0.25


def _v(**k) -> Verdict:
    base: dict = {"drank": False, "lifted": False, "at_mouth": False, "tilted": False, "lowered": False,
                  "confidence": 0.9, "at": 0.0, "ms": 1}
    base.update(k)
    return Verdict(**base)


def test_combine_raises_a_seen_drink() -> None:
    c, ev, why = combine(0.1, "held up blue mug", _v(drank=True, at_mouth=True, tilted=True, lowered=True))
    assert c == 0.8 and why == "raised" and "frame check" in ev


def test_combine_caps_a_held_mug(monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_VERIFY_VETO", "1")
    c, _, why = combine(0.9, "mug at mouth", _v(at_mouth=True, tilted=False))
    assert c == 0.45 and why == "held_not_tilted"
    monkeypatch.setenv("HEARTH_VERIFY_VETO", "0")
    assert combine(0.9, "mug at mouth", _v(at_mouth=True))[0] == 0.9


def test_combine_leaves_unsure_or_missing_alone() -> None:
    assert combine(0.3, "x", None)[:2] == (0.3, "x")
    assert combine(0.3, "x", _v(drank=True, at_mouth=True, tilted=True, confidence=0.5))[0] == 0.3
    assert combine(0.7, "x", _v(lifted=True))[0] == 0.7


def test_judge_without_key_is_a_no_op(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(sipjudge, "_client", None)
    frames = [(float(i), b"\xff\xd8x") for i in range(5)]
    assert asyncio.run(sipjudge.judge(frames)) is None
    assert asyncio.run(sipjudge.judge(frames[:2])) is None
