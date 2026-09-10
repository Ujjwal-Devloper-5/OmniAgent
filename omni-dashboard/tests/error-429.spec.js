import { test, expect } from '@playwright/test';

test.describe('429 Rate Limit Interception', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('omni_token', 'valid-admin-secret');
    });
    await page.route('**/api/status', async (route) => {
      await route.fulfill({ status: 200, json: { status: 'healthy', providers: {} } });
    });
    await page.route('**/api/health', async (route) => {
      await route.fulfill({ status: 200, json: { status: 'ok' } });
    });
    await page.route('**/api/sessions', async (route) => {
      await route.fulfill({ status: 200, json: [] });
    });
    await page.route('**/api/models', async (route) => {
      await route.fulfill({ status: 200, json: { models: [] } });
    });
    await page.route('**/api/mcp/status', async (route) => {
      await route.fulfill({ status: 200, json: { available: true, servers: {}, total_tools: 0 } });
    });
  });

  test('displays rate limit notification on 429 response without logging out', async ({ page }) => {
    await page.route('**/api/config/update', async (route) => {
      await route.fulfill({
        status: 429,
        contentType: 'application/json',
        headers: { 'Retry-After': '60' },
        body: JSON.stringify({ detail: 'Too many requests. Please wait 60 seconds.' }),
      });
    });

    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          bot_name: 'OmniAgent',
          routing_policy: 'AUTO',
          sandbox_ttl_seconds: 300,
          sandbox_max_concurrent: 10,
        },
      });
    });

    await page.goto('/config');
    await expect(page.locator('h2:has-text("Configuration")')).toBeVisible();

    const saveButton = page.locator('button:has-text("Save"), button:has-text("Update")');
    await expect(saveButton).toBeVisible();
    await saveButton.click();

    // Verify rate limit toast or alert appears
    const rateLimitNotice = page.locator('text=/rate limit|too many requests|429/i').first();
    await expect(rateLimitNotice).toBeVisible();

    // Verify token was NOT evicted
    const token = await page.evaluate(() => localStorage.getItem('omni_token'));
    expect(token).toBe('valid-admin-secret');

    // Verify user remains on the authenticated dashboard page
    await expect(page.locator('input[type="password"]')).not.toBeVisible();
  });
});
