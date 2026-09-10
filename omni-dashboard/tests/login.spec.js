import { test, expect } from '@playwright/test';

test.describe('Authentication Flow', () => {
  test.beforeEach(async ({ page }) => {
    // Clear localStorage before each test
    await page.addInitScript(() => window.localStorage.clear());
  });

  test('displays login screen when unauthenticated and blocks dashboard access', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button:has-text("Sign In")')).toBeVisible();
    await expect(page.locator('text=OmniAgent')).toBeVisible();
    await expect(page.locator('nav')).not.toBeVisible();
  });

  test('handles invalid token with 401 response and shows error message', async ({ page }) => {
    await page.route('**/api/status', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid token' }),
      });
    });

    await page.goto('/');
    await page.fill('input[type="password"]', 'wrong-token');
    await page.click('button:has-text("Sign In")');

    await expect(page.locator('text=Invalid token or Admin API is not reachable')).toBeVisible();
    const token = await page.evaluate(() => localStorage.getItem('omni_token'));
    expect(token).toBeFalsy();
    await expect(page.locator('button:has-text("Sign In")')).toBeVisible();
  });

  test('successfully logs in with valid token, persists token, and renders dashboard', async ({ page }) => {
    await page.route('**/api/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'healthy',
          providers: {
            openai: { configured: true, healthy: true, capabilities: ['general'] },
            anthropic: { configured: true, healthy: true, capabilities: ['general'] },
          },
        }),
      });
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

    await page.goto('/');
    await page.fill('input[type="password"]', 'valid-admin-secret');
    await page.click('button:has-text("Sign In")');

    // Verify token stored in localStorage
    const token = await page.evaluate(() => localStorage.getItem('omni_token'));
    expect(token).toBe('valid-admin-secret');

    // Verify dashboard rendered
    await expect(page.locator('text=Active Sessions')).toBeVisible();
    await expect(page.locator('h1:has-text("Overview")')).toBeVisible();
  });
});
