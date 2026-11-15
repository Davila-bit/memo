"""WCAG 2.2 contrast ratios for the Hearth palette. Run: python3 web/scripts/contrast.py"""


def lum(h: str) -> float:
    h = h.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def cr(a: str, b: str) -> float:
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


BG = {"paper": "#F6F1E9", "surface": "#FFFDF8"}
TINTS = {"sage-tint": "#E7EFEA", "amber-tint": "#F8ECD9", "coral-tint": "#F8E4E0", "sky-tint": "#E3EDF2"}
FG = {
    "ink": "#1F2A2E",
    "muted": "#5B6670",
    "sage (spec)": "#5E8C7B",
    "amber (spec)": "#B9771F",
    "coral (spec)": "#C8483B",
    "sky (spec)": "#3B7EA1",
    "sage-ink": "#3D6A5A",
    "amber-ink": "#87530F",
    "coral-ink": "#A8372C",
    "sky-ink": "#2A6383",
}

if __name__ == "__main__":
    print("| text | on | ratio | AA small (4.5) |")
    print("|---|---|---|---|")
    for bn, bv in {**BG, **TINTS}.items():
        for fn, fv in FG.items():
            if bn in TINTS and "spec" in fn:
                continue
            r = cr(fv, bv)
            print(f"| {fn} `{fv}` | {bn} `{bv}` | {r:.2f} | {'pass' if r >= 4.5 else 'FAIL'} |")
    pairs = [
        ("surface on sage-ink (button)", "#FFFDF8", "#3D6A5A"),
        ("surface on coral-ink (button)", "#FFFDF8", "#A8372C"),
        ("surface on ink (button)", "#FFFDF8", "#1F2A2E"),
        ("ink on amber fill", "#1F2A2E", "#D98E2B"),
        ("non-text: sage ring vs paper", "#5E8C7B", "#F6F1E9"),
        ("non-text: sky ring vs surface", "#3B7EA1", "#FFFDF8"),
        ("non-text: amber fill vs paper", "#D98E2B", "#F6F1E9"),
        ("non-text: focus ring sky-ink vs paper", "#2A6383", "#F6F1E9"),
        ("non-text: border line vs surface", "#8C949A", "#FFFDF8"),
    ]
    for n, a, b in pairs:
        print(f"| {n} | | {cr(a, b):.2f} | |")
