"""Plain, warm sentences for the timeline, alerts and notifications (docs/08 §2 wording rules:
never "detected a fall", never "swallowed")."""

from __future__ import annotations

from datetime import datetime

DRINK = {
    "sip": "Had a sip of water",
    "some": "Drank some water",
    "most": "Drank most of a glass of water",
    "all": "Drank a full glass of water",
}
DRINK_BODY = {"sip": "had a sip", "some": "drank some", "most": "drank most of a glass", "all": "drank a full glass"}
MEAL_AMOUNT = {"sip": "had a bite or two", "some": "ate some of it", "most": "ate most of it", "all": "ate all of it"}


def meal_name(label: str | None, when: datetime | None = None, tz: str | None = None) -> str:
    text = (label or "").lower()
    for word in ("breakfast", "lunch", "dinner", "supper", "tea"):
        if word in text:
            return word
    if when is not None and tz:
        from zoneinfo import ZoneInfo
        h = when.astimezone(ZoneInfo(tz)).hour
        return "breakfast" if h < 11 else "lunch" if h < 16 else "dinner"
    return "a meal"


def med_period(label: str | None, when: datetime | None = None, tz: str | None = None) -> str:
    text = (label or "").lower()
    if "morning" in text:
        return "Morning"
    if "evening" in text or "night" in text:
        return "Evening"
    if when is not None and tz:
        from zoneinfo import ZoneInfo
        return "Morning" if when.astimezone(ZoneInfo(tz)).hour < 14 else "Evening"
    return "Her"


def drink_summary(amount: str) -> str:
    return DRINK.get(amount, "Drank some water")


def meal_summary(amount: str, meal: str) -> str:
    return f"Had {meal}: {MEAL_AMOUNT.get(amount, 'ate some of it')}"


def med_summary(period: str) -> str:
    return f"{period} tablets: observed taking"


def what_for(kind: str, label: str | None = None) -> str:
    """'water' / 'lunch' / 'morning tablets'"""
    if kind == "drink":
        return "water"
    if kind == "meal":
        return meal_name(label)
    return f"{med_period(label).lower()} tablets" if med_period(label) != "Her" else "tablets"
