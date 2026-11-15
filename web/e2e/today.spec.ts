import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn, startCheckin } from "./helpers";

test.describe("sign-in", () => {
  test("wrong passcode shows a described error; right one opens Today", async ({ page }, info) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Family sign-in" })).toBeVisible();
    await expectAccessible(page, info, "login");
    await shot(page, info, "01-login");

    await page.getByLabel("Passcode").fill("wrong");
    await page.getByRole("button", { name: "Sign in" }).click();
    const err = page.getByRole("alert").filter({ hasText: "didn't work" });
    await expect(err).toBeVisible();
    const describedBy = await page.getByLabel("Passcode").getAttribute("aria-describedby");
    const errId = await err.getAttribute("id");
    expect(describedBy).toContain(errId!);
    await expect(page.getByLabel("Passcode")).toHaveAttribute("aria-invalid", "true");
    await expectAccessible(page, info, "login-error");
    await shot(page, info, "02-login-error");

    await page.getByLabel("Passcode").fill("hearth-demo");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { level: 1, name: /Mum has had 640 ml today/ })).toBeVisible();
  });
});

test.describe("today", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("today renders; check-in walks Calling → She answered → Watching → ✓ in the timeline", async ({ page }, info) => {
    await expect(page.getByText("Next: water at 3:30 pm")).toBeVisible();
    await expect(page.getByTestId("timeline").getByRole("listitem").first()).toContainText("few sips");
    await expectAccessible(page, info, "today");
    await shot(page, info, "03-today");
    await shot(page, info, "03b-today-full", true);

    await page.getByRole("button", { name: "Check on her now" }).click();
    await expect(page.getByRole("dialog", { name: "Check on Mum now" })).toBeVisible();
    await expectAccessible(page, info, "checkin-sheet");
    await shot(page, info, "04-checkin-sheet");
    await page.keyboard.press("Escape");

    await startCheckin(page, "She has it");
    const card = page.getByTestId("live-checkin");
    await expect(card).toBeVisible();
    await expect(page.getByRole("button", { name: "Check on her now" })).toBeDisabled();
    await expect(card.locator('[aria-current="step"]')).toContainText("Calling");
    await shot(page, info, "05-live-calling");

    await expect(card).toHaveAttribute("data-status", "connected");
    await expect(card.locator('[aria-current="step"]')).toContainText("She answered");

    await expect(card).toHaveAttribute("data-status", "watching");
    await expect(card.getByRole("log", { name: "Live captions" })).toContainText("Helper:");
    await expect(card.getByRole("log")).toContainText("Mum:");
    await expect(card).toContainText("Live only. Nothing is recorded.");
    await expectAccessible(page, info, "live-watching");
    await shot(page, info, "06-live-watching");

    await expect(card).toHaveAttribute("data-outcome", "confirmed", { timeout: 15_000 });
    await expect(card).toHaveAttribute("data-status", "ended");
    await expect(card.getByRole("heading", { name: /Mum drank most of a glass/ })).toBeVisible();
    await expect(card).toContainText("What the helper saw:");
    await expect(card).toContainText("Seen clearly");
    await expect(page.getByRole("heading", { level: 1, name: /Mum has had 820 ml today/ })).toBeVisible();
    await expectAccessible(page, info, "result-confirmed");
    await shot(page, info, "07-result-confirmed");

    const first = page.getByTestId("timeline").getByRole("listitem").first();
    await expect(first).toContainText("Mum drank most of a glass");
    await first.getByRole("button", { name: "What the helper saw" }).click();
    await expect(first).toContainText("Held up the blue mug");
    await card.getByRole("button", { name: "Done" }).click();
    await expect(card).toBeHidden();
  });

  test("refusal shows the alert and 'Try again in 30 min' handles it", async ({ page }, info) => {
    await startCheckin(page, "She says no");
    const alert = page.getByTestId("alert-refused");
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText("I'm not thirsty, I had tea");
    await expectAccessible(page, info, "alert-refused");
    await shot(page, info, "08-alert-refused");

    await alert.getByRole("button", { name: "Try again in 30 min" }).click();
    await expect(alert).toBeHidden();
    await expect(page.getByText("The helper will try again in 30 minutes.").first()).toBeVisible();
    await expect(page.getByTestId("timeline").getByRole("listitem").first()).toContainText("You asked the helper to try again later");
  });

  test("spoken report shows Said so without adding observed water or an alert", async ({ page }, info) => {
    await startCheckin(page, "She says she did");
    const card = page.getByTestId("live-checkin");
    await expect(card).toHaveAttribute("data-status", "ended");
    await expect(card).toHaveAttribute("data-outcome", "reported");
    await expect(card.getByRole("heading", { name: /Mum says she's had her water/ })).toBeVisible();
    await expect(card).toContainText("The helper heard her say so but couldn't see her on this call.");
    await expect(card).toContainText("Said so");
    await expect(card).not.toContainText("What the helper saw");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("640 ml today");
    await expect(page.getByRole("region", { name: "Needs you" })).toHaveCount(0);
    const item = page.getByTestId("timeline").getByRole("listitem").first();
    await expect(item).toContainText("Said so");
    await expect(item).toContainText("Said she had some water.");
    await item.getByRole("button", { name: "What the helper heard" }).click();
    await expect(item).toContainText("I've had my water, thank you");
    await expect(item).not.toContainText("Seen clearly");
    await expectAccessible(page, info, "result-reported");
  });

  test("notification alert link focuses the card when it arrives, once", async ({ page }) => {
    // Set the URL before an alert exists, so rendering the alert must trigger focus.
    await page.evaluate(() => window.history.pushState(null, "", "/?alert=not-here"));
    await startCheckin(page, "She says no");
    const alert = page.getByTestId("alert-refused");
    await expect(alert).toBeVisible();
    const target = await alert.getAttribute("id");
    expect(target).toBeTruthy();
    await page.evaluate((id) => window.history.pushState(null, "", `/?alert=${id.slice("alert-".length)}`), target!);
    await expect(alert).toBeFocused();
    await expect(alert).toBeInViewport();
    const button = page.getByRole("button", { name: "Check on her now" });
    await expect(button).toBeEnabled();
    await button.focus();
    // A poll/store refresh must not pull focus back to the notification.
    await page.waitForTimeout(1600);
    await expect(button).toBeFocused();
  });

  test("no answer shows the amber alert", async ({ page }, info) => {
    await startCheckin(page, "No answer");
    const alert = page.getByTestId("alert-no_answer");
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText("didn't answer");
    await expectAccessible(page, info, "alert-no-answer");
    await shot(page, info, "09-alert-no-answer");
    await alert.getByRole("button", { name: "I'll call her" }).click();
    await expect(alert).toBeHidden();
  });

  test("concern shows the coral urgent alert with 911 as her own tap", async ({ page }, info) => {
    await startCheckin(page, "Something's wrong");
    const alert = page.getByTestId("alert-concern");
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toHaveAttribute("role", "alert");
    await expect(alert).toContainText("The check-in noticed something unusual: she said ‘I fell down, I can't get up’ at");
    await expect(alert).toContainText("Please check on her now.");
    await expect(alert.getByRole("link", { name: "Call 911" })).toHaveAttribute("href", "tel:911");
    await expectAccessible(page, info, "alert-concern");
    await shot(page, info, "10-alert-concern");
    await alert.getByRole("button", { name: "I'm handling it" }).click();
    await expect(alert).toBeHidden();
  });

  test("add a note and large type", async ({ page }, info) => {
    await page.getByRole("button", { name: "Add a note" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a note" });
    await dialog.getByRole("button", { name: "Add to today" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Please write a few words first.");
    await dialog.getByLabel("Your note").fill("Popped round with soup. She was cheerful.");
    await dialog.getByRole("button", { name: "Add to today" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId("timeline").getByRole("listitem").first()).toContainText("Popped round with soup");

    await page.getByRole("button", { name: "Settings" }).click();
    const settings = page.getByRole("dialog", { name: "Settings" });
    await settings.getByRole("switch", { name: "Larger text" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-large-type", "1");
    await expectAccessible(page, info, "settings");
    await shot(page, info, "11-settings-large-type");
    await page.keyboard.press("Escape");
    await expectAccessible(page, info, "today-large-type");
    await shot(page, info, "12-today-large-type");
  });

  test("demo clock moves time on", async ({ page }) => {
    await page.getByRole("button", { name: /Demo clock/ }).click();
    const dialog = page.getByRole("dialog", { name: "Demo clock" });
    await dialog.getByRole("button", { name: "+1 h" }).click();
    await expect(page.getByText("Next: water at 6:00 pm")).toBeVisible();
    await dialog.getByRole("button", { name: "Reset", exact: true }).click();
    await expect(page.getByText("Next: water at 3:30 pm")).toBeVisible();
  });
});

test("keyboard only: sign in, start a check-in, see the result", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "keyboard run on the laptop size");
  await page.goto("/login");
  await page.keyboard.press("Tab"); // passcode input
  await expect(page.getByLabel("Passcode")).toBeFocused();
  await page.keyboard.type("hearth-demo");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: /has had/ })).toBeVisible();

  // Skip link is the first stop.
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeVisible();

  const cta = page.getByRole("button", { name: "Check on her now" });
  for (let i = 0; i < 25 && !(await cta.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(cta).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // Tab into the radio group; Water is the default. Then on to the call button.
  const call = dialog.getByRole("button", { name: /Call .* now/ });
  for (let i = 0; i < 20 && !(await call.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  await expect(call).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  const card = page.getByTestId("live-checkin");
  await expect(card).toHaveAttribute("data-outcome", "confirmed", { timeout: 15_000 });
  await shot(page, info, "13-keyboard-result");
});
