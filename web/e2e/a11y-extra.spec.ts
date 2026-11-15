import { expect, test, type Locator, type Page } from "@playwright/test";
import { shot, signIn } from "./helpers";

/*
 * Accessibility checks axe can't make: reflow (WCAG 1.4.10), reduced motion (2.3.3) and keyboard
 * use with a visible focus indicator (2.1.1, 2.4.7). Each sets its own viewport, so it runs once,
 * in the laptop project.
 */
test.beforeEach(({}, info) => {
  test.skip(info.project.name !== "laptop", "sets its own viewport; runs once");
});

const PAGES: { path: string; name: string; ready: (page: Page) => Locator }[] = [
  { path: "/", name: "today", ready: (p) => p.getByRole("heading", { level: 1, name: /has had/ }) },
  { path: "/week", name: "week", ready: (p) => p.getByTestId("fluid-bars") },
  { path: "/schedule", name: "schedule", ready: (p) => p.getByRole("heading", { name: "Check-in times" }) },
  { path: "/ask", name: "ask", ready: (p) => p.getByRole("heading", { level: 1 }) },
  { path: "/privacy", name: "privacy", ready: (p) => p.getByTestId("kept-list") },
  { path: "/setup", name: "setup", ready: (p) => p.getByRole("navigation", { name: "Setup progress" }) },
];

/** Widest thing on the page that sticks out past the viewport, for a useful failure message. */
async function overflow(page: Page): Promise<{ scrollWidth: number; innerWidth: number; culprits: string[] }> {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const culprits: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 1 && culprits.length < 5) {
        const cls = typeof el.className === "string" ? el.className.split(" ").slice(0, 4).join(".") : "";
        culprits.push(`${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${cls ? `.${cls}` : ""} (right ${Math.round(r.right)})`);
      }
    }
    return { scrollWidth: document.documentElement.scrollWidth, innerWidth: vw, culprits };
  });
}

for (const width of [320, 640]) {
  test(`reflow: no horizontal scrolling at ${width} px wide`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 800 });

    await page.goto("/login");
    await expect(page.getByLabel("Passcode")).toBeVisible();
    let o = await overflow(page);
    expect(o.scrollWidth, `login at ${width}px overflows: ${o.culprits.join(", ")}`).toBeLessThanOrEqual(o.innerWidth + 1);
    await shot(page, info, `reflow-${width}-login`, true);

    await signIn(page);
    for (const p of PAGES) {
      await page.goto(p.path);
      await expect(p.ready(page)).toBeVisible();
      await page.waitForTimeout(300);
      o = await overflow(page);
      expect(o.scrollWidth, `${p.name} at ${width}px overflows: ${o.culprits.join(", ")}`).toBeLessThanOrEqual(o.innerWidth + 1);
      await shot(page, info, `reflow-${width}-${p.name}`, true);
    }
  });
}

test("reduced motion: nothing runs longer than 200 ms, and a check-in still finishes", async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await signIn(page);

  // Spinners allowed to keep turning (name them here if one is ever needed). None today.
  const ALLOWED_INFINITE: string[] = [];
  const long = () =>
    page.evaluate((allowed) => {
      return document
        .getAnimations()
        .filter((a) => a.playState === "running")
        .map((a) => {
          const t = a.effect?.getComputedTiming();
          const target = (a.effect as KeyframeEffect | null)?.target as Element | null;
          const name = (a as CSSAnimation).animationName || (a as CSSTransition).transitionProperty || a.id || "motion";
          return {
            name,
            duration: Number(t?.duration ?? 0),
            infinite: t?.iterations === Infinity,
            where: target ? `${target.tagName.toLowerCase()}.${String(target.getAttribute("class") ?? "").split(" ").slice(0, 3).join(".")}` : "?",
          };
        })
        .filter((a) => (a.infinite ? !allowed.includes(a.name) : a.duration > 200));
    }, ALLOWED_INFINITE);

  const offenders: string[] = [];
  const sample = async (when: string) => {
    for (const a of await long()) offenders.push(`${when}: ${a.name} ${a.infinite ? "infinite" : `${a.duration} ms`} on ${a.where}`);
  };

  await sample("today");
  await page.getByRole("button", { name: "Check on her now" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await sample("sheet open");
  await dialog.getByText("Water", { exact: true }).click();
  await dialog.getByText("Practice", { exact: true }).click();
  await dialog.getByText("She has it", { exact: true }).click();
  await dialog.getByRole("button", { name: /Call .* now/ }).click();
  await expect(dialog).toBeHidden();

  const card = page.getByTestId("live-checkin");
  await expect(card).toBeVisible();
  await sample("calling");
  await expect(card).toHaveAttribute("data-status", "connected");
  await sample("connected");
  await expect(card).toHaveAttribute("data-status", "watching");
  await sample("watching");
  await expect(card).toHaveAttribute("data-outcome", "confirmed", { timeout: 15_000 });
  await sample("result");
  await shot(page, info, "reduced-motion-result");

  expect(offenders, "animations still running under prefers-reduced-motion").toEqual([]);
});

interface Stop {
  tag: string;
  label: string;
  visible: boolean;
  indicator: boolean;
  isBody: boolean;
}

/** Tab through the page once and describe every stop. */
async function tabCycle(page: Page, max = 80): Promise<Stop[]> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const stops: Stop[] = [];
  let first: string | null = null;
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const s = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return { key: "body", tag: "body", label: "", visible: false, indicator: false, isBody: true };
      if (!el.dataset.tabKey) el.dataset.tabKey = Math.random().toString(36).slice(2);
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow !== "none" && cs.boxShadow !== "";
      const type = el.getAttribute("type");
      const named = el.getAttribute("aria-label") || el.textContent?.trim() || el.getAttribute("name") || el.id || "";
      const label = `${type ? `[${type}] ` : ""}${named}`.trim().slice(0, 60);
      return {
        key: el.dataset.tabKey,
        tag: el.tagName.toLowerCase(),
        label,
        visible: r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.opacity !== "0",
        indicator: outline || shadow,
        isBody: false,
      };
    });
    if (s.isBody) {
      if (stops.length > 0) break; // walked off the end of the page: one full cycle
      continue;
    }
    if (first === null) first = s.key;
    else if (s.key === first) break; // wrapped round to the first stop
    stops.push({ tag: s.tag, label: s.label, visible: s.visible, indicator: s.indicator, isBody: false });
  }
  return stops;
}

for (const p of PAGES.filter((x) => ["/week", "/schedule", "/privacy", "/setup"].includes(x.path))) {
  test(`keyboard: every Tab stop on ${p.name} is visible and shows focus`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await signIn(page);
    await page.goto(p.path);
    await expect(p.ready(page)).toBeVisible();
    await page.waitForTimeout(300);

    const stops = await tabCycle(page);
    expect(stops.length, `no Tab stops found on ${p.name}`).toBeGreaterThan(3);
    const bad = stops
      .map((s, i) => ({ ...s, i }))
      .filter((s) => !s.visible || !s.indicator)
      .map((s) => `#${s.i} <${s.tag}> "${s.label}"${s.visible ? "" : " not visible"}${s.indicator ? "" : " no focus indicator"}`);
    expect(bad, `${p.name}: Tab stops without a visible focus indicator`).toEqual([]);
  });
}

/** Press Tab until `target` has focus (fails after `max` presses). */
async function tabTo(page: Page, target: Locator, max = 60): Promise<void> {
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

test("keyboard: add a check-in on Schedule without the mouse", async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page);
  await page.goto("/schedule");
  await expect(page.getByRole("heading", { name: "Check-in times" })).toBeVisible();

  await tabTo(page, page.getByRole("button", { name: "Add a check-in", exact: true }));
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Add a check-in" });
  await expect(dialog).toBeVisible();

  const label = dialog.getByLabel("Label", { exact: true });
  await tabTo(page, label);
  await page.keyboard.type("Tea with the radio on");

  const time = dialog.getByLabel("Time", { exact: true });
  await tabTo(page, time);
  await page.keyboard.type("0415P"); // hh, mm, am/pm segments of a time field
  await expect(time).toHaveValue("16:15");

  await tabTo(page, dialog.getByRole("button", { name: "Save check-in" }));
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  const added = page.getByRole("switch", { name: "Tea with the radio on check-in" });
  await expect(added).toBeVisible();
  await shot(page, info, "keyboard-schedule-added");
});
