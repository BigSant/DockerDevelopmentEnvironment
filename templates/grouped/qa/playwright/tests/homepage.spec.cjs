const { test, expect } = require('@playwright/test');

test('parduotuvės pagrindinis puslapis pasiekiamas', async ({ page }) => {
  const response = await page.goto('/');
  expect(response).not.toBeNull();
  expect(response.status()).toBeLessThan(400);
  await expect(page.locator('body')).toBeVisible();
});
