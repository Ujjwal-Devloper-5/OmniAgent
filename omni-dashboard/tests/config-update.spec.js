import { test, expect } from '@playwright/test';

test.describe('Configuration Update Flow', () => {
  test.beforeEach(async ({ page }) => {
    // Inject pre-authenticated token
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

    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          bot_name: 'OmniAgent',
          routing_policy: 'AUTO',
          sandbox_ttl_seconds: 300,
          sandbox_max_concurrent: 10,
          log_level: 'INFO',
        },
      });
    });
  });

  test('successfully submits configuration update to /api/config/update', async ({ page }) => {
    let capturedRequest = null;

    await page.route('**/api/config/update', async (route) => {
      capturedRequest = {
        headers: route.request().headers(),
        postData: JSON.parse(route.request().postData() || '{}'),
      };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          updated: capturedRequest.postData,
          errors: {},
        }),
      });
    });

    await page.goto('/config');
    await expect(page.locator('h2:has-text("Configuration")')).toBeVisible();

    // Select or change editable field (e.g. routing policy)
    const policySelect = page.locator('select[name="routing_policy"], select#routing_policy');
    await expect(policySelect).toBeVisible();
    await policySelect.selectOption('SPEED');

    // Submit the update form
    const saveButton = page.locator('button:has-text("Save"), button:has-text("Update")');
    await expect(saveButton).toBeVisible();
    await saveButton.click();

    // Verify network interception
    expect(capturedRequest).not.toBeNull();
    expect(capturedRequest.headers['authorization']).toBe('Bearer valid-admin-secret');
    expect(capturedRequest.postData.routing_policy).toBe('SPEED');

    // Verify success toast or confirmation message
    await expect(page.locator('text=Configuration updated')).toBeVisible();
  });
});
