import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn } from "./helpers";

// The Week page as a printed one-page summary. Laptop project only; sets its own viewport.
test.beforeEach(({}, info) => {
  test.skip(info.project.name !== "laptop", "sets its own viewport; runs once");
});

test("week prints as one clean page: shell hidden, header, chart, table and footer shown", async ({ page }, info) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page);
  await page.goto("/week");
  await expect(page.getByTestId("fluid-bars")).toBeVisible();

  // On screen: the button is there and named; the print-only parts are not.
  const print = page.getByRole("button", { name: "Print this week" });
  await expect(print).toBeVisible();
  await expect(page.getByTestId("print-header")).toBeHidden();
  await expect(page.getByTestId("print-footer")).toBeHidden();
  await expectAccessible(page, info, "week-print");

  // A4 is 794 px wide at 96 dpi.
  await page.setViewportSize({ width: 794, height: 1123 });
  await page.emulateMedia({ media: "print" });
  for (const nav of await page.getByRole("navigation", { name: "Main" }).all()) await expect(nav).toBeHidden();
  await expect(page.getByRole("banner")).toBeHidden();
  await expect(print).toBeHidden();

  const header = page.getByTestId("print-header");
  await expect(header).toBeVisible();
  await expect(header).toContainText("Memo: weekly check-in summary");
  await expect(header).toContainText("Margaret Ellis");
  await expect(header).toContainText(/\w+ \d+ to \w+ \d+, \d{4}/);
  await expect(header).toContainText(/Printed \w+ \d+, \d{4}/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("fluid-bars").getByRole("listitem")).toHaveCount(7);
  await expect(page.getByTestId("day-table").locator("tbody tr")).toHaveCount(7);
  await expect(page.getByTestId("print-footer")).toContainText("This is not a medical record.");

  // Bars stay solid and dark on paper.
  const fill = await page.locator("[data-bar]").last().evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(fill).toBe("rgb(31, 42, 46)");

  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
  expect(o.sw, "no horizontal overflow at A4 width").toBeLessThanOrEqual(o.iw + 1);
  await shot(page, info, "week-print", true);

  // The real print path: an A4 PDF of the seeded week is exactly one page.
  const pdf = await page.pdf({ format: "A4", printBackground: false, preferCSSPageSize: true });
  const pages = (pdf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) ?? []).length;
  expect(pages, "printed pages").toBe(1);
});
