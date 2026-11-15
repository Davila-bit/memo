import { expect, test } from "@playwright/test";
import { expectAccessible, shot, signIn } from "./helpers";

test.beforeEach(async ({ page }) => { await signIn(page); await page.getByRole("link", { name: "Ask", exact: true }).click(); });

test("ask a question, follow matching moments and reuse recent questions", async ({ page }, info) => {
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Ask about Mum's day");
  await expect(page.getByText("Ask anything about what the helper has logged")).toBeVisible();
  await expectAccessible(page, info, "ask-empty");
  await shot(page, info, "ask-empty");

  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByLabel("Your question")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("alert").filter({ hasText: "Please write a question" })).toBeVisible();

  await page.getByRole("button", { name: "How much has she drunk today?", exact: true }).click();
  await expect(page.getByText("Simple search", { exact: true })).toBeVisible();
  await expect(page.getByText("The helper observed about 640 ml today.")).toBeVisible();
  await expect(page.getByTestId("ask-hits").getByRole("listitem").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent questions" })).toBeVisible();
  await expectAccessible(page, info, "ask-answer");
  await shot(page, info, "ask-answer", true);

  await page.getByLabel("Your question").fill("zzzzzzzz");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("No matching moments. Try another question or a different day.")).toBeVisible();
  await expectAccessible(page, info, "ask-no-matches");
  await page.getByRole("button", { name: "How much has she drunk today?", exact: true }).last().click();
  await expect(page.getByTestId("ask-hits")).toBeVisible();
});

test("Enter submits a question and Shift+Enter inserts a newline", async ({ page }) => {
  const question = page.getByLabel("Your question");
  await question.fill("How much has she drunk today?");
  await question.press("Shift+Enter");
  await expect(question).toHaveValue("How much has she drunk today?\n");
  await expect(page.getByText("From the log", { exact: true })).toHaveCount(0);
  await question.press("Enter");
  await expect(page.getByText("The helper observed about 640 ml today.")).toBeVisible();
  await expect(question).toHaveValue("How much has she drunk today?\n");
});
