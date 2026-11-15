"""Demo seed: Margaret, her schedule and cues, and 6 days + today-so-far of believable history.
Deterministic (seeded RNG) so every demo looks the same."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
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
        phone=None,
        fluid_target_ml=1500,
        timezone=TZ,
        quiet_hours=("21:00", "07:00"),
        consent=Consent(status="signed", signed_by="Margaret Ellis", relation="self",
                        signed_at=datetime(2026, 11, 10, 15, 0, tzinfo=UTC)),
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
        ("med-0815", "med", "08:15", "Morning tablets", "cue-pills", True),
        ("meal-0830", "meal", "08:30", "Breakfast", "cue-plate"),
        ("water-1030", "drink", "10:30", "Mid-morning water", "cue-mug"),
        ("meal-1230", "meal", "12:30", "Lunch", "cue-plate"),
        ("water-1300", "drink", "13:00", "Early afternoon water", "cue-mug"),
        ("water-1530", "drink", "15:30", "Afternoon water", "cue-mug"),
        ("meal-1730", "meal", "17:30", "Dinner", "cue-plate"),
        ("water-1800", "drink", "18:00", "Evening water", "cue-mug"),
        ("med-1900", "med", "19:00", "Evening tablets", "cue-pills", False),  # off: one tablet time a day
    ]
    return [ScheduleItem(id=r[0], resident_id=RESIDENT_ID, kind=r[1], time=r[2], label=r[3], cue_id=r[4],
                         enabled=r[5] if len(r) > 5 else True)
            for r in rows]


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


def history(now: datetime, items: list[ScheduleItem]) -> list[HearthEvent]:
    """6 previous local days plus today up to `now`, for the check-ins that are switched on."""
    rng = random.Random(1500)
    today = now.astimezone(ZoneInfo(TZ)).date()
    out: list[HearthEvent] = []
    # Five water slots each day. Complete days rise gently from 550 to 1020 ml;
    # None marks a refusal. Today is partial, never padded with future drinks.
    drink_amounts = {
        6: ["some", "most", "some", "most", "sip"],
        5: ["most", "some", "most", None, "most"],
        4: ["most", "most", "some", "most", "some"],
        3: ["all", "most", None, "most", "most"],
        2: ["all", "most", "all", "most", "some"],
        1: ["all", "most", "most", "all", "most"],
        0: ["all", "most", "most", "all", "all"],
    }
    drink_slots = [i.id for i in items if i.kind == "drink"]

    for back in range(6, -1, -1):
        d = today - timedelta(days=back)
        for item in (i for i in items if i.enabled):
            ts = at_local(d, item.time, TZ) + timedelta(minutes=rng.randint(1, 3), seconds=rng.randint(0, 59))
            if ts > now:
                continue
            eid = f"ev_seed_{d.isoformat()}_{item.id}"
            base = {"id": eid, "ts": ts, "resident_id": RESIDENT_ID, "source": "seed", "for_kind": item.kind}
            if (back, item.id) in {(5, "water-1530"), (3, "water-1300")}:
                said = "not thirsty, had tea" if back == 5 else "said she'd have some later"
                out.append(HearthEvent(**base, kind="refused", summary="Said no to her afternoon water",
                                       said=said, confidence=1.0))
                continue
            if (back, item.id) in {(4, "med-0815"), (2, "med-0815")}:
                at = "8:15"
                out.append(HearthEvent(**base, kind="missed",
                                       summary=f"Didn't answer the {at} tablets check-in (tried twice)"))
                continue
            if item.kind == "drink":
                amount = drink_amounts[back][drink_slots.index(item.id)]
                out.append(HearthEvent(**base, kind="drink", summary=wording.drink_summary(amount),
                                        amount=amount, volume_ml_est=AMOUNT_ML[amount],
                                        confidence=round(rng.uniform(0.78, 0.97), 2),
                                        evidence=rng.choice(DRINK_EVIDENCE)))
            elif item.kind == "meal":
                amount = rng.choice(["most", "all", "some", "most"])
                meal = wording.meal_name(item.label)
                out.append(HearthEvent(**base, kind="meal", summary=wording.meal_summary(amount, meal),
                                        amount=amount, confidence=round(rng.uniform(0.75, 0.95), 2),
                                        evidence=rng.choice(MEAL_EVIDENCE)))
            else:
                out.append(HearthEvent(**base, kind="med",
                                        summary=wording.med_summary(wording.med_period(item.label)),
                                        med_steps=list(MED_STEPS), confidence=round(rng.uniform(0.8, 0.95), 2),
                                        evidence=MED_EVIDENCE))
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
