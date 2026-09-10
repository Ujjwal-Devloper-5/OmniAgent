import { test, expect } from '@playwright/test';

test.describe('401 Unauthorized Network Interception', () => {
  test('evicts token from storage and returns user to login on 401 response without infinite reload', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('omni_token', 'expired-secret-token');
    });

    // Mock an authenticated resource returning 401 Unauthorized
    await page.route('**/api/status', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid token' }),
      });
    });
    await page.route('**/api/sessions', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid token' }),
      });
    });
    await page.route('**/api/models', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid token' }),
      });
    });

    await page.goto('/');

    // Verify token was evicted from localStorage
    await expect.poll(async () => {
      return await page.evaluate(() => localStorage.getItem('omni_token'));
    }).toBeNull();

    // Verify UI cleanly displays login screen
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button:has-text("Sign In")')).toBeVisible();
  });
});
