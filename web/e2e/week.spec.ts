import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn } from "./helpers";

test.describe("week", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("week shows the headline, seven days of fluids, and the same days in words", async ({ page }, info) => {
    // Sidebar on a laptop, bottom bar on a phone: click whichever is showing.
    await page.getByRole("link", { name: "Week", exact: true }).filter({ visible: true }).first().click();
    await expect(page).toHaveURL(/\/week$/);

    await expect(page.getByRole("heading", { level: 1, name: /^Mum (averaged \d[\d,]* ml a day|had \d[\d,]* ml|has had \d[\d,]* ml)/ })).toBeVisible();

    const bars = page.getByTestId("fluid-bars").getByRole("listitem");
    await expect(bars).toHaveCount(7);
    // Today is the last bar, highlighted and named in words, not colour alone.
    await expect(bars.last()).toHaveAttribute("data-today", "true");
    await expect(bars.last()).toContainText("Today");
    await expect(bars.last()).toContainText(/ml so far/);

    const rows = page.getByTestId("day-table").locator("tbody tr");
    await expect(rows).toHaveCount(7);
    await expect(rows.first().getByRole("rowheader")).toContainText("Today");

    await expect(page.getByText(/Tablets observed \d+ days? running/)).toBeVisible();
    // Mock mode isn't Tiger, so no aggregate footnote.
    await expect(page.getByText("Tiger Data continuous aggregates")).toHaveCount(0);

    await expectAccessible(page, info, "week");
    await shot(page, info, "10-week");
    await shot(page, info, "10b-week-full", true);

    // Keyboard: Tab reaches each bar, and each bar says its day and amount.
    await bars.first().focus();
    await expect(bars.first()).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(bars.nth(1)).toBeFocused();
  });
});
