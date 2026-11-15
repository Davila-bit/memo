import { expect, test } from "@playwright/test";
import { expectAccessible, signIn } from "./helpers";

test("watch is hidden by default; opt-in turns on, shows notes and turns off", async ({ page }, info) => {
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Keep an eye on Mum" })).toHaveCount(0);
  await page.goto("/?mockWatch=1");
  const card = page.getByRole("region", { name: "Keep an eye on Mum" });
  await expect(card).toBeVisible();
  await expectAccessible(page, info, "watch-off");
  const start = card.getByRole("button", { name: "30 minutes" });
  await expect(start).toBeEnabled();
  await start.focus();
  await page.keyboard.press("Enter");
  await expect(card.getByRole("status")).toContainText("On until");
  await expect(card.getByRole("log", { name: "Watch notes" })).toContainText("Sitting at the table");
  await expect(card.getByRole("log")).toContainText("OK");
  await expectAccessible(page, info, "watch-on");
  await card.getByRole("button", { name: "Turn off" }).click();
  await expect(start).toBeEnabled();
  await expect(card.getByRole("button", { name: "Turn off" })).toHaveCount(0);
  await expect(card.getByRole("log")).toBeEmpty();
  await expectAccessible(page, info, "watch-stopped");
});
