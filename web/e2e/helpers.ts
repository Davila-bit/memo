import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type TestInfo } from "@playwright/test";
import path from "node:path";

export const SHOTS = path.join(__dirname, "screenshots");

/** Signs in through the real form (mock passcode). */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Passcode").fill("hearth-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /has had \d+ ml today/ })).toBeVisible();
}

/** Full-page screenshot named <project>-<state>.png under e2e/screenshots/. */
export async function shot(page: Page, info: TestInfo, state: string): Promise<void> {
  await page.waitForTimeout(350); // let entrance animations settle
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-${state}.png`), fullPage: true });
}

export interface AxeSummary {
  state: string;
  serious: string[];
  other: string[];
}

export const axeLog: AxeSummary[] = [];

/** Fails on serious/critical axe violations (WCAG 2.2 A/AA rules); logs the rest. */
export async function expectAccessible(page: Page, info: TestInfo, state: string): Promise<void> {
  // Measure contrast after entrance fades finish, not mid-animation.
  await page.waitForTimeout(700);
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
  const serious = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const other = res.violations.filter((v) => !(v.impact === "serious" || v.impact === "critical"));
  const fmt = (v: (typeof res.violations)[number]) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`;
  const line = `[axe] ${info.project.name} ${state}: ${serious.length} serious/critical, ${other.length} minor/moderate, ${res.passes.length} rules passed`;
  console.log(line);
  for (const v of other) console.log(`   minor: ${fmt(v)}`);
  for (const v of serious) console.log(`   SERIOUS: ${fmt(v)}`);
  expect(serious.map(fmt), `serious axe violations on ${state}`).toEqual([]);
}

/** Opens the check-in sheet and starts a practice check-in with the chosen ending. */
export async function startCheckin(page: Page, ending: "She has it" | "She says no" | "No answer" | "Something's wrong", kind = "Water"): Promise<void> {
  await page.getByRole("button", { name: "Check on her now" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByText(kind, { exact: true }).click();
  await dialog.getByText("Practice", { exact: true }).click();
  await dialog.getByText(ending, { exact: true }).click();
  await dialog.getByRole("button", { name: /Call .* now/ }).click();
  await expect(dialog).toBeHidden();
}
