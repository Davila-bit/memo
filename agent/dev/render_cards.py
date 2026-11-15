"""Render every cue-card state to PNGs for a visual check (synthetic content only, no call data).

    uv run python dev/render_cards.py OUT_DIR
"""

import sys
from pathlib import Path

from hearth_agent.cuecard import CardState, render

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cap = "Hello Margaret, it's Sarah's helper. Could you hold up your mug so I can see it?"
states = {
    "waiting": CardState(mode="waiting"),
    "asking": CardState(mode="asking", line="Time for some water, Margaret", caption=cap),
    "watching": CardState(mode="watching", line="Time for some water, Margaret", caption="Lovely. Have a drink."),
    "med": CardState(mode="asking", kind="med", slot_label="TUE · MORNING", line="Your morning tablets",
                     caption="Could you open the Tuesday morning box?"),
    "meal": CardState(mode="watching", kind="meal", line="Lunch time, Margaret", caption="Show me your plate."),
    "thanks": CardState(mode="thanks", caption="Thank you, Margaret. Sarah will be pleased. Goodbye."),
    "okay": CardState(mode="okay", caption="That's all right, I'll let Sarah know."),
    "concern": CardState(mode="concern", caption="I'm going to get Sarah for you right now. Stay where you are."),
}
for name, st in states.items():
    render(st).save(out / f"{name}.png")
print("wrote", len(states), "cards to", out)
