import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn } from "./helpers";

test.describe("privacy", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
    // Sidebar on a laptop, bottom bar on a phone: click whichever is showing.
    await page.getByRole("link", { name: "Privacy", exact: true }).filter({ visible: true }).first().click();
    await expect(page).toHaveURL(/\/privacy$/);
    await expect(page.getByRole("heading", { level: 1, name: "What Memo keeps, and what it never keeps." })).toBeVisible();
  });

  test("shows both lists, in-transit services, retention and consent", async ({ page }, info) => {
    const kept = page.getByTestId("kept-list").getByRole("listitem");
    const never = page.getByTestId("never-list").getByRole("listitem");
    await expect(kept.first()).toBeVisible();
    await expect(kept.first()).toContainText("Kept");
    await expect(never.first()).toContainText("Never kept");
    expect(await kept.count()).toBeGreaterThan(0);
    expect(await never.count()).toBeGreaterThan(0);

    await expect(page.getByTestId("privacy-counts")).toContainText(/\d+ check-in notes?/);
    await expect(page.getByText("Live video and sound pass through these services during the call and are not stored by Memo.")).toBeVisible();
    await expect(page.getByText(/Check-in notes are deleted after \d+ days\./)).toBeVisible();
    await expect(page.getByTestId("consent-line")).toContainText("Agreed by Margaret Ellis herself");
    await expect(page.getByText("Signed", { exact: true })).toBeVisible();

    await expectAccessible(page, info, "privacy");
    await shot(page, info, "11-privacy");
    await shot(page, info, "11b-privacy-full", true);
  });

  test("delete dialog explains what goes, cancels with Escape, then deletes with a toast", async ({ page }, info) => {
    const open = page.getByRole("button", { name: "Delete everything…" });
    await open.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Delete everything?" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/All \d+ check-in notes about Mum/);
    await expect(dialog).toContainText("Every check-in record and alert");
    // The destructive button is never the one focused when the dialog opens.
    await expect(dialog.getByRole("button", { name: "Delete everything" })).not.toBeFocused();
    await expectAccessible(page, info, "privacy-delete-dialog");
    await shot(page, info, "12-privacy-delete-dialog");

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(open).toBeFocused();

    await open.click();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Delete everything" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText(/Deleted \d+ check-in notes?\./)).toBeVisible();
    await expect(page.getByTestId("privacy-counts")).toContainText("0 check-in notes");
  });
});
