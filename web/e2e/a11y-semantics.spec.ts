import { expect, test, type Locator, type Page } from "@playwright/test";
import { shot, signIn, startCheckin } from "./helpers";

/*
 * What a screen reader depends on (structure, names, live announcements, focus return) and
 * Windows forced colours (high contrast). Automated stand-ins: they are not a pass with a real
 * screen reader. Each test sets its own viewport, so the file runs once, in the laptop project.
 */
test.beforeEach(({}, info) => {
  test.skip(info.project.name !== "laptop", "sets its own viewport; runs once");
});

const PAGES: { path: string; name: string; title: RegExp; ready: (page: Page) => Locator }[] = [
  { path: "/", name: "today", title: /^Today · Memo$/, ready: (p) => p.getByRole("heading", { level: 1, name: /has had/ }) },
  { path: "/week", name: "week", title: /^Week · Memo$/, ready: (p) => p.getByTestId("fluid-bars") },
  { path: "/schedule", name: "schedule", title: /^Schedule · Memo$/, ready: (p) => p.getByRole("heading", { name: "Check-in times" }) },
  { path: "/ask", name: "ask", title: /^Ask · Memo$/, ready: (p) => p.getByRole("heading", { level: 1 }) },
  { path: "/privacy", name: "privacy", title: /^Privacy · Memo$/, ready: (p) => p.getByTestId("kept-list") },
  { path: "/setup", name: "setup", title: /^Setup · Memo$/, ready: (p) => p.getByRole("navigation", { name: "Setup progress" }) },
];

interface Structure {
  lang: string | null;
  h1: number;
  skips: string[];
  mains: number;
  banners: number;
  navs: { name: string }[];
  unnamedNavs: number;
}

/** Headings in reading order (only those exposed to assistive tech), landmarks, lang. */
function readStructure(page: Page): Promise<Structure> {
  return page.evaluate(() => {
    const exposed = (el: Element) => {
      if (el.closest("[aria-hidden='true'], [hidden], [inert]")) return false;
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") return false;
      // display:none on an ancestor
      return (el as HTMLElement).offsetParent !== null || cs.position === "fixed";
    };
    const headings = Array.from(document.querySelectorAll<HTMLElement>("h1, h2, h3, h4, h5, h6, [role='heading']")).filter(exposed);
    const levels = headings.map((h) => ({
      level: h.getAttribute("role") === "heading" ? Number(h.getAttribute("aria-level") ?? 2) : Number(h.tagName[1]),
      text: (h.textContent ?? "").trim().slice(0, 40),
    }));
    const skips: string[] = [];
    let prev = 0;
    for (const h of levels) {
      if (h.level > prev + 1) skips.push(`h${prev || "-"} → h${h.level} "${h.text}"`);
      prev = h.level;
    }
    const navs = Array.from(document.querySelectorAll<HTMLElement>("nav, [role='navigation']")).filter(exposed);
    const name = (el: HTMLElement) => {
      const by = el.getAttribute("aria-labelledby");
      if (by) return by.split(" ").map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim();
      return (el.getAttribute("aria-label") ?? "").trim();
    };
    const banners = Array.from(document.querySelectorAll("header, [role='banner']")).filter(
      (h) => exposed(h) && !h.parentElement?.closest("article, aside, main, nav, section, [role='region']"),
    );
    return {
      lang: document.documentElement.getAttribute("lang"),
      h1: levels.filter((h) => h.level === 1).length,
      skips,
      mains: Array.from(document.querySelectorAll("main, [role='main']")).filter(exposed).length,
      banners: banners.length,
      navs: navs.map((n) => ({ name: name(n) })),
      unnamedNavs: navs.filter((n) => !name(n)).length,
    };
  });
}

const CONTROL_ROLES = "button|link|textbox|checkbox|switch|combobox|spinbutton|radio|searchbox|slider|menuitem|tab";

/** Controls in the accessibility tree that have no name (from Playwright's ARIA snapshot). */
async function unnamedControls(page: Page): Promise<string[]> {
  const snap = await page.locator("body").ariaSnapshot();
  const re = new RegExp(`^\\s*- (${CONTROL_ROLES})(?:\\s*\\[[^\\]]*\\])*\\s*(?::|$)`);
  return snap.split("\n").filter((l) => re.test(l)).map((l) => l.trim());
}

/**
 * Two different controls with the same role and name in the same region would sound identical.
 * Region = nearest labelled section/landmark/dialog/list item.
 */
async function duplicateNames(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
    const accName = (el: HTMLElement): string => {
      const by = el.getAttribute("aria-labelledby");
      if (by) return by.split(" ").map((id) => text(document.getElementById(id))).join(" ").trim();
      const al = el.getAttribute("aria-label");
      if (al) return al.trim();
      if (el.id) {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) return text(lab);
      }
      const wrap = el.closest("label");
      if (wrap) return text(wrap);
      return text(el) || el.getAttribute("title") || "";
    };
    const sel = "button, a[href], input:not([type='hidden']), select, textarea, [role='button'], [role='link'], [role='switch'], [role='checkbox']";
    const regionSel = "[role='dialog'], [role='alert'], [role='region'], section[aria-labelledby], section[aria-label], nav, header, aside, main, li, article, form, fieldset";
    const seen = new Map<string, Set<string>>();
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      if (el.closest("[aria-hidden='true'], [inert]")) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || (el.offsetParent === null && cs.position !== "fixed")) continue;
      const region = el.closest(regionSel);
      const regionKey = region ? `${region.tagName}#${region.id || ""}:${text(region).slice(0, 30)}` : "page";
      const role = el.getAttribute("role") ?? el.tagName.toLowerCase();
      const name = accName(el);
      const purpose = el.getAttribute("href") ?? el.getAttribute("type") ?? "";
      const key = `${regionKey}|${role}|${name}`;
      const purposes = seen.get(key) ?? new Set<string>();
      if (purposes.size > 0 && !purposes.has(purpose)) out.push(`${role} "${name}" twice in ${regionKey}`);
      purposes.add(purpose);
      seen.set(key, purposes);
    }
    return out;
  });
}

test("structure and names: headings, landmarks, titles, lang and named controls on every page", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const titles = new Map<string, string>();
  const problems: string[] = [];

  const check = async (name: string, titleRe: RegExp) => {
    const s = await readStructure(page);
    if (s.lang !== "en") problems.push(`${name}: <html lang> is "${s.lang}"`);
    if (s.h1 !== 1) problems.push(`${name}: ${s.h1} h1 elements`);
    for (const k of s.skips) problems.push(`${name}: heading level skipped ${k}`);
    if (s.mains !== 1) problems.push(`${name}: ${s.mains} main landmarks`);
    if (name !== "login" && s.banners < 1) problems.push(`${name}: no banner/header`);
    if (name !== "login" && s.navs.length < 1) problems.push(`${name}: no navigation landmark`);
    if (s.unnamedNavs) problems.push(`${name}: ${s.unnamedNavs} navigation landmark(s) without a name`);
    const title = await page.title();
    if (!titleRe.test(title)) problems.push(`${name}: title "${title}" doesn't name the page`);
    titles.set(name, title);
    for (const c of await unnamedControls(page)) problems.push(`${name}: unnamed control ${c}`);
    for (const d of await duplicateNames(page)) problems.push(`${name}: ${d}`);
  };

  await page.goto("/login");
  await expect(page.getByLabel("Passcode")).toBeVisible();
  await check("login", /^Sign in · Memo$/);

  await signIn(page);
  for (const p of PAGES) {
    await page.goto(p.path);
    await expect(p.ready(page)).toBeVisible();
    await page.waitForTimeout(300);
    await check(p.name, p.title);
  }

  const values = [...titles.values()];
  expect(new Set(values).size, `page titles must be unique: ${values.join(" | ")}`).toBe(values.length);
  expect(problems).toEqual([]);
});

/** Record every text change inside live regions from now on. */
async function recordLiveRegions(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __live: { kind: string; text: string }[] };
    w.__live = [];
    const kindOf = (el: Element): string | null => {
      const live = el.closest("[aria-live], [role='status'], [role='alert'], [role='log']");
      if (!live) return null;
      const role = live.getAttribute("role");
      const aria = live.getAttribute("aria-live");
      if (aria === "off") return null;
      return role === "alert" || aria === "assertive" ? "assertive" : "polite";
    };
    new MutationObserver((muts) => {
      for (const m of muts) {
        const nodes = m.type === "characterData" ? [m.target] : Array.from(m.addedNodes);
        for (const n of nodes) {
          const el = n.nodeType === Node.ELEMENT_NODE ? (n as Element) : n.parentElement;
          if (!el) continue;
          // A newly inserted alert (or one inside an inserted subtree) is announced on arrival.
          if (n.nodeType === Node.ELEMENT_NODE) {
            const added = n as Element;
            const alerts = [added.matches("[role='alert']") ? added : null, ...Array.from(added.querySelectorAll("[role='alert']"))].filter(Boolean) as Element[];
            for (const a of alerts) {
              const t = (a.textContent ?? "").replace(/\s+/g, " ").trim();
              if (t) w.__live.push({ kind: "assertive", text: t });
            }
            if (alerts.length) continue;
          }
          const kind = kindOf(el);
          if (!kind) continue;
          const t = (n.textContent ?? "").replace(/\s+/g, " ").trim();
          if (t) w.__live.push({ kind, text: t });
        }
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
}

const announced = (page: Page) => page.evaluate(() => (window as unknown as { __live: { kind: string; text: string }[] }).__live);

test("announcements: check-in stages and the result reach a live region; focus comes back from the sheet", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page);

  // The sheet gives focus back to the button that opened it.
  const cta = page.getByRole("button", { name: "Check on her now" });
  await cta.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(cta).toBeFocused();

  await recordLiveRegions(page);
  await startCheckin(page, "She has it");
  // While the call runs the button is disabled, so focus goes to the live card's heading instead.
  await expect(page.getByRole("heading", { name: /^Checking on Mum/ })).toBeFocused();
  const card = page.getByTestId("live-checkin");
  await expect(card).toHaveAttribute("data-outcome", "confirmed", { timeout: 15_000 });
  await page.waitForTimeout(500);

  const polite = (await announced(page)).filter((a) => a.kind === "polite").map((a) => a.text).join(" | ");
  for (const stage of ["Calling", "She answered", "Watching"]) expect(polite, `"${stage}" announced`).toContain(stage);
  expect(polite, "result announced").toMatch(/drank|had|sip|water/i);
});

test("announcements: a new refusal alert is assertive", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page);
  await recordLiveRegions(page);
  await startCheckin(page, "She says no");
  const alert = page.getByTestId("alert-refused");
  await expect(alert).toBeVisible({ timeout: 15_000 });
  await expect(alert).toHaveAttribute("role", "alert");
  await page.waitForTimeout(300);
  const assertive = (await announced(page)).filter((a) => a.kind === "assertive").map((a) => a.text).join(" | ");
  expect(assertive).toContain("I'm not thirsty");
});

/** System colours as this browser resolves them in forced-colours mode. */
const systemColours = (page: Page) =>
  page.evaluate(() => {
    const probe = document.createElement("div");
    document.body.append(probe);
    const read = (c: string) => {
      probe.style.backgroundColor = c;
      return getComputedStyle(probe).backgroundColor;
    };
    const out = { canvas: read("Canvas"), highlight: read("Highlight"), grayText: read("GrayText") };
    probe.remove();
    return out;
  });

async function expectFocusRing(page: Page, target: Locator): Promise<void> {
  await target.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(target).toBeFocused();
  const outline = await target.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth) };
  });
  expect(outline.style, "focus outline in forced colours").not.toBe("none");
  expect(outline.width).toBeGreaterThan(0);
}

/** Every status badge still shows an icon and a word. */
async function expectBadges(page: Page): Promise<void> {
  const bad = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("span.rounded-full.border.font-semibold"))
      .filter((b) => b.querySelector("svg"))
      .filter((b) => {
        const svg = b.querySelector("svg")!.getBoundingClientRect();
        const word = (b.textContent ?? "").trim();
        const cs = getComputedStyle(b);
        return !(svg.width > 0 && svg.height > 0 && word && cs.color !== cs.backgroundColor);
      })
      .map((b) => (b.textContent ?? "").trim()),
  );
  expect(bad, "badges missing an icon or a word in forced colours").toEqual([]);
}

test("forced colours: Today with an alert, Week and Schedule keep focus rings, badges, ring and bars", async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ forcedColors: "active" });
  await signIn(page);

  // Today, with an open alert.
  await startCheckin(page, "She says no");
  await expect(page.getByTestId("alert-refused")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(400);
  await shot(page, info, "forced-today", true);
  await expectFocusRing(page, page.getByRole("button", { name: "Try again in 30 min" }));
  await expectBadges(page);
  const sys = await systemColours(page);
  const ring = await page.evaluate((c) => {
    const svg = document.querySelector("svg[data-ring]");
    if (!svg || svg.getBoundingClientRect().width === 0) return ["no ring drawn"];
    const circles = Array.from(svg.querySelectorAll("circle")).map((el) => getComputedStyle(el).stroke);
    const problems: string[] = [];
    if (circles.length < 2) problems.push(`only ${circles.length} ring circles`);
    if (circles[0] !== c.grayText) problems.push(`track stroke ${circles[0]} is not GrayText`);
    if (circles.at(-1) !== c.highlight) problems.push(`progress stroke ${circles.at(-1)} is not Highlight`);
    if (circles.some((s) => s === "none" || s === c.canvas)) problems.push("a ring stroke matches the background");
    return problems;
  }, sys);
  expect(ring, "hydration ring in forced colours").toEqual([]);

  // Week: the seven bars still stand out from the background.
  await page.goto("/week");
  await expect(page.getByTestId("fluid-bars")).toBeVisible();
  await page.waitForTimeout(400);
  await shot(page, info, "forced-week", true);
  await expectBadges(page);
  const bars = await page.evaluate((c) => {
    return Array.from(document.querySelectorAll("[data-testid='fluid-bars'] li")).map((li) => {
      const bar = li.querySelector<HTMLElement>("[data-bar]");
      if (!bar) return "no [data-bar]";
      if (bar.getBoundingClientRect().height === 0) return "empty"; // a day with nothing to draw
      const bgc = getComputedStyle(bar).backgroundColor;
      return bgc === c.highlight && bgc !== c.canvas ? "drawn" : `not Highlight (${bgc})`;
    });
  }, sys);
  expect(bars.filter((b) => b !== "drawn" && b !== "empty"), "week bars in forced colours").toEqual([]);
  expect(bars.filter((b) => b === "drawn").length).toBeGreaterThan(0);
  await expectFocusRing(page, page.getByTestId("fluid-bars").getByRole("listitem").first());

  // Schedule.
  await page.goto("/schedule");
  await expect(page.getByRole("heading", { name: "Check-in times" })).toBeVisible();
  await page.waitForTimeout(400);
  await shot(page, info, "forced-schedule", true);
  await expectBadges(page);
  await expectFocusRing(page, page.getByRole("button", { name: "Add a check-in", exact: true }));
});
