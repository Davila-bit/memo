"""~40 events over 4 days (Mon 9 – Thu 12 Nov 2026, America/New_York); "now" is Thu 3:30 pm."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from hearth_contracts.models import AMOUNT_ML, HearthEvent

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 12, 15, 30, tzinfo=TZ).astimezone(UTC)
DAYS = [datetime(2026, 11, d, tzinfo=TZ) for d in (9, 10, 11, 12)]  # Mon, Tue, Wed, Thu


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return day.replace(hour=h, minute=m).astimezone(UTC)


def events() -> list[HearthEvent]:
    out: list[HearthEvent] = []

    def add(day: datetime, hhmm: str, kind: str, summary: str, **kw) -> None:
        ts = _at(day, hhmm)
        if ts > NOW:
            return
        if kind == "drink" and "amount" in kw:
            kw.setdefault("volume_ml_est", AMOUNT_ML[kw["amount"]])
        out.append(HearthEvent(id=f"ev-{len(out) + 1:03d}", ts=ts, resident_id="margaret", kind=kind,
                               source="seed", summary=summary, **kw))

    mugs = {"sip": "Took a sip of water", "some": "Drank some water", "most": "Drank most of a glass of water",
            "all": "Drank a whole glass of water"}
    for i, d in enumerate(DAYS):
        add(d, "08:00", "drink", mugs["some"], amount="some", confidence=0.9,
            evidence="Held up the blue mug, drank twice, mug lower after")
        add(d, "08:15", "med", "Took her morning tablets (observed)", confidence=0.92,
            med_steps=["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water"],
            evidence="Opened the morning slot, pill to mouth, sip of water")
        add(d, "08:30", "meal", "Ate most of her breakfast", amount="most", confidence=0.85,
            evidence="Toast and porridge, plate mostly empty")
        if (i, "10:30") != (3, "10:30"):
            add(d, "10:30", "drink", mugs["sip" if i == 1 else "most"], amount="sip" if i == 1 else "most",
                confidence=0.8, evidence="Glass raised to mouth, small drink")
        else:
            add(d, "10:30", "missed", "Didn't answer her 10:30 water check-in", confidence=1.0)
        add(d, "12:15", "meal", "Ate most of her lunch" if i != 0 else "Ate some of her lunch",
            amount="most" if i != 0 else "some", confidence=0.85, evidence="Sandwich, half eaten, then most")
        add(d, "14:00", "drink", mugs["all"], amount="all", confidence=0.88, evidence="Finished the glass of juice")
        if i == 2:
            add(d, "14:10", "refused", "Didn't want her 2:10 water", confidence=1.0,
                said="not thirsty, had tea with Mrs [name] next door")
        if i == 0:
            add(d, "16:00", "missed", "Didn't answer her 4:00 water check-in", confidence=1.0)
        else:
            add(d, "16:00", "drink", mugs["most"], amount="most", confidence=0.86, evidence="Mug up, two sips")
        add(d, "18:00", "meal", "Ate some of her dinner", amount="some", confidence=0.8, evidence="Soup, some left")
        if i == 3:
            continue
        if i == 0:
            add(d, "19:04", "refused", "Didn't want her evening tablets", confidence=1.0,
                said="I've already had them, dear")
        else:
            add(d, "19:04", "med", "Took her evening tablets (observed)", confidence=0.93,
                med_steps=["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"],
                evidence="TUE EVENING slot shown empty after" if i == 1 else "Evening slot empty after")
        add(d, "19:30", "drink", mugs["some"], amount="some", confidence=0.84, evidence="Cup of tea, a few sips")
    return out


# Thursday so far: 08:00 some (80) + 14:00 all (240) = 320 ml over 2 drinks (10:30 missed).
TODAY_ML = 80 + 240
TODAY_DRINKS = 2
