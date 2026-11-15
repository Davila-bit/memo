import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn } from "./helpers";

test("setup saves each step and runs a practice check-in", async ({ page }, info) => {
  await signIn(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("dialog", { name: "Settings", exact: true }).getByRole("link", { name: "Setup", exact: true }).click();
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByLabel("Her full name")).toHaveValue("Margaret Ellis");
  await expectAccessible(page, info, "setup");
  await shot(page, info, "setup", true);

  await page.getByLabel("Her full name").fill("");
  await page.getByRole("button", { name: "Save names", exact: true }).click();
  await expect(page.getByLabel("Her full name")).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel("Her full name").fill("Margaret Ellis");
  await page.getByLabel("What you call her").fill("Mama");
  await page.getByLabel("What the helper calls her").fill("Margaret");
  await page.getByRole("button", { name: "Save names", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Getting to know Mama/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Setup progress" })).toContainText("1 of 4 steps complete");

  await page.getByLabel("Daily fluid target (ml)").fill("0");
  await page.getByRole("button", { name: "Save daily target", exact: true }).click();
  await expect(page.getByLabel("Daily fluid target (ml)")).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel("Daily fluid target (ml)").fill("1600");
  await page.getByRole("button", { name: "Save daily target", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Setup progress" })).toContainText("2 of 4 steps complete");

  await page.getByLabel("Who agreed to the check-ins?").selectOption("healthcare_agent");
  await page.getByLabel("Their full name").fill("Sarah Ellis");
  await page.getByLabel("Date they agreed").fill("2026-11-14");
  await page.getByRole("button", { name: "Save consent record", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Setup progress" })).toContainText("3 of 4 steps complete");
  await expect(page.getByRole("link", { name: "Read about privacy" })).toHaveAttribute("href", "/privacy");
  await expectAccessible(page, info, "setup-saved");
  await shot(page, info, "setup-saved", true);

  await page.getByRole("button", { name: "Run a practice check-in", exact: true }).click();
  await expect(page.getByText("Practice started. Open Today to follow the check-in.")).toBeVisible();
  await page.getByRole("link", { name: "Go to Today", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Mama has had");
  await expect(page.getByTestId("live-checkin")).toHaveAttribute("data-outcome", "confirmed", { timeout: 15_000 });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("dialog", { name: "Settings", exact: true }).getByRole("link", { name: "Setup", exact: true }).click();
  await expect(page.getByLabel("What you call her")).toHaveValue("Mama");
  await expect(page.getByLabel("Daily fluid target (ml)")).toHaveValue("1600");
  await expect(page.getByLabel("Who agreed to the check-ins?")).toHaveValue("healthcare_agent");
  await expect(page.getByLabel("Their full name")).toHaveValue("Sarah Ellis");
  await expect(page.getByLabel("Date they agreed")).toHaveValue("2026-11-14");
});

test("setup shows the kitchen iPad checklist", async ({ page }, info) => {
  await signIn(page);
  await page.goto("/setup");
  const card = page.locator("#setup-ipad");
  await expect(page.getByRole("heading", { level: 2, name: "The kitchen iPad" })).toBeVisible();
  await expect(card.getByRole("checkbox")).toHaveCount(5);
  await expect(card.getByRole("status")).toHaveText("0 of 5 done");

  const first = page.getByLabel(/Turn on Auto-Answer\./);
  await first.check();
  await expect(first).toBeChecked();
  await expect(card.getByRole("status")).toHaveText("1 of 5 done");

  // Keyboard: Tab to the next box and tick it with Space.
  await first.focus();
  await page.keyboard.press("Tab");
  const second = page.getByLabel(/Save the helper as a contact\./);
  await expect(second).toBeFocused();
  await page.keyboard.press("Space");
  await expect(second).toBeChecked();
  await expect(card.getByRole("status")).toHaveText("2 of 5 done");
  await page.keyboard.press("Space");
  await expect(second).not.toBeChecked();

  await page.reload();
  await expect(page.getByLabel(/Turn on Auto-Answer\./)).toBeChecked();
  await expect(card.getByRole("status")).toHaveText("1 of 5 done");

  await expectAccessible(page, info, "setup-ipad");
  await shot(page, info, "setup-ipad", true);
});
