import { test, expect } from "@playwright/test";

async function login(page: any) {
  await page.goto("/login");
  await page.getByRole("textbox", { name: "输入邮箱" }).fill("admin@tradeclaw.local");
  await page.getByRole("textbox", { name: "输入密码" }).fill("admin123");
  await page.getByRole("button", { name: "登录" }).click();
  await page.waitForURL(/\/overview/);
}

test.describe("Overview Page", () => {
  test("renders the page structure", async ({ page }) => {
    await login(page);

    // Verify main sections exist
    await expect(page.getByText("策略净值曲线")).toBeVisible();
    await expect(page.getByText("智能体实时状态")).toBeVisible();
    await expect(page.getByText("实时信号队列")).toBeVisible();
    await expect(page.getByText("智能体通信日志")).toBeVisible();
  });

  test("metrics section loads real data", async ({ page }) => {
    await login(page);

    // Metrics cards should show labels (even if values are 0 when DB is empty)
    await expect(page.getByText("净值")).toBeVisible();
    await expect(page.getByText("今日盈亏")).toBeVisible();
    await expect(page.getByText("活跃订单")).toBeVisible();
  });

  test("agent cards load from API", async ({ page }) => {
    await login(page);

    // Should see agent names from the API
    await expect(page.getByText("主控编排")).toBeVisible();
    await expect(page.getByText("市场分析")).toBeVisible();
    await expect(page.getByText("交易执行")).toBeVisible();
  });

  test("no React errors on page load", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await login(page);
    await page.waitForTimeout(2000);

    // No React-level errors expected
    expect(errors).toHaveLength(0);
  });
});
