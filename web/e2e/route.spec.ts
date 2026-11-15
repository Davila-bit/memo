import { expect, test } from "@playwright/test";
import { expectAccessible, signIn } from "./helpers";

test("Auto is demo-only, keeps Practice selected, and shows the accessible route hint", async ({ page }, info) => {
  await signIn(page);
  await page.goto("/?demo=0");
  await page.getByRole("button", { name: "Check on her now" }).click();
  await expect(page.getByRole("radio", { name: "Auto", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.goto("/?demo=1");
  await page.getByRole("button", { name: "Check on her now" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("radio", { name: "Practice", exact: true })).toBeChecked();
  const auto = dialog.getByRole("radio", { name: "Auto", exact: true });
  await expect(auto).not.toBeChecked();
  await dialog.getByText("Auto", { exact: true }).click();
  await expect(auto).toBeChecked();
  await expect(dialog.getByRole("status")).toHaveText("Kitchen screen is up: Memo will use it.");
  await expectAccessible(page, info, "auto-route");
  await dialog.getByRole("button", { name: "Call Mum now" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("live-checkin")).toBeVisible();
});
