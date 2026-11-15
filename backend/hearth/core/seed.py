"""Demo seed: Margaret, her schedule and cues, and 3 days + today-so-far of believable history.
Deterministic (seeded RNG) so every demo looks the same."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hearth_contracts import AMOUNT_ML, Consent, Cue, HearthEvent, Resident, ScheduleItem

from . import wording
from .clock import at_local
from .store import Store

RESIDENT_ID = "margaret"
TZ = "America/New_York"


def resident() -> Resident:
    return Resident(
        id=RESIDENT_ID,
        name="Margaret Ellis",
        preferred_name="Margaret",
        family_name_for_her="Mum",
        daughter_name="Sarah",
        fluid_target_ml=1500,
        timezone=TZ,
        quiet_hours=("21:00", "07:00"),
        consent=Consent(status="signed", signed_by="Margaret Ellis", relation="self",
                        signed_at=datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)),
    )


def cues() -> list[Cue]:
    return [
        Cue(id="cue-mug", kind="drink", title="Your blue mug", line="Time for some water, Margaret"),
        Cue(id="cue-plate", kind="meal", title="Your lunch plate", line="Time for something to eat, Margaret"),
        Cue(id="cue-pills", kind="med", title="Your pill organiser", line="Time for your tablets, Margaret"),
    ]


def schedule() -> list[ScheduleItem]:
    rows = [
        ("water-0800", "drink", "08:00", "Morning water", "cue-mug"),
        ("med-0815", "med", "08:15", "Morning tablets", "cue-pills"),
        ("meal-0830", "meal", "08:30", "Breakfast", "cue-plate"),
        ("water-1030", "drink", "10:30", "Mid-morning water", "cue-mug"),
        ("meal-1230", "meal", "12:30", "Lunch", "cue-plate"),
        ("water-1300", "drink", "13:00", "Early afternoon water", "cue-mug"),
        ("water-1530", "drink", "15:30", "Afternoon water", "cue-mug"),
        ("meal-1730", "meal", "17:30", "Dinner", "cue-plate"),
        ("water-1800", "drink", "18:00", "Evening water", "cue-mug"),
        ("med-1900", "med", "19:00", "Evening tablets", "cue-pills"),
    ]
    return [ScheduleItem(id=i, resident_id=RESIDENT_ID, kind=k, time=t, label=lbl, cue_id=c)
            for i, k, t, lbl, c in rows]


DRINK_EVIDENCE = [
    "Held the blue mug up, drank twice, mug lower after",
    "Showed the mug, took a few sips, showed it again",
    "Lifted the mug to her mouth, drank, set it down nearly empty",
    "Showed the mug, one long drink, mug lower after",
]
MEAL_EVIDENCE = [
    "Showed the plate, ate several forkfuls",
    "Plate in view, eating steadily, plate mostly clear after",
    "Showed a bowl, ate with a spoon",
]
MED_EVIDENCE = "Opened the slot, tablets to mouth, sip of water, slot shown empty"
MED_STEPS = ["slot_opened", "pill_in_hand", "hand_to_mouth", "drank_water", "slot_shown_empty"]


def history(now: datetime, items: list[ScheduleItem]) -> list[tuple[HearthEvent, str | None]]:
    """3 previous local days plus today up to `now`."""
    rng = random.Random(1500)
    today = now.astimezone(ZoneInfo(TZ)).date()
    out: list[tuple[HearthEvent, str | None]] = []
    drink_amounts = ["most", "all", "some", "most", "sip", "most", "all", "some"]

    for back in (3, 2, 1, 0):
        d = today - timedelta(days=back)
        for item in items:
            ts = at_local(d, item.time, TZ) + timedelta(minutes=rng.randint(1, 3), seconds=rng.randint(0, 59))
            if ts > now:
                continue
            eid = f"ev_seed_{d.isoformat()}_{item.id}"
            base = dict(id=eid, ts=ts, resident_id=RESIDENT_ID, source="seed")
            # The three notable moments in the history.
            if back == 2 and item.id == "water-1530":
                out.append((HearthEvent(**base, kind="refused", summary="Said no to her afternoon water",
                                        said="not thirsty, had tea", confidence=1.0), "drink"))
                continue
            if back == 1 and item.id == "water-1300":
                out.append((HearthEvent(**base, kind="missed",
                                        summary="Didn't answer the 1:00 check-in (tried twice)"), "drink"))
                continue
            if back == 3 and item.id == "med-1900":
                out.append((HearthEvent(**base, kind="note", confidence=0.4,
                                        summary="Evening tablets: not seen on the call",
                                        evidence="Pill box stayed closed; she said she'd do it later"), "med"))
                continue
            if item.kind == "drink":
                amount = drink_amounts[rng.randrange(len(drink_amounts))]
                out.append((HearthEvent(**base, kind="drink", summary=wording.drink_summary(amount),
                                        amount=amount, volume_ml_est=AMOUNT_ML[amount],
                                        confidence=round(rng.uniform(0.78, 0.97), 2),
                                        evidence=rng.choice(DRINK_EVIDENCE)), "drink"))
            elif item.kind == "meal":
                amount = rng.choice(["most", "all", "some", "most"])
                meal = wording.meal_name(item.label)
                out.append((HearthEvent(**base, kind="meal", summary=wording.meal_summary(amount, meal),
                                        amount=amount, confidence=round(rng.uniform(0.75, 0.95), 2),
                                        evidence=rng.choice(MEAL_EVIDENCE)), "meal"))
            else:
                out.append((HearthEvent(**base, kind="med",
                                        summary=wording.med_summary(wording.med_period(item.label)),
                                        med_steps=list(MED_STEPS), confidence=round(rng.uniform(0.8, 0.95), 2),
                                        evidence=MED_EVIDENCE), "med"))
    return out


async def seed(store: Store, now: datetime) -> int:
    await store.wipe()
    r = resident()
    await store.put_resident(r)
    for c in cues():
        await store.put_cue(c)
    items = schedule()
    for i in items:
        await store.put_schedule_item(i)
    evs = history(now, items)
    await store.add_events(evs)
    # Items already past today count as run, so the schedule tick doesn't replay them.
    today = now.astimezone(ZoneInfo(TZ)).date().isoformat()
    ran = [i.id for i in items if at_local(now.astimezone(ZoneInfo(TZ)).date(), i.time, TZ) <= now]
    await store.kv_set(f"ran:{today}", ran)
    return len(evs)
