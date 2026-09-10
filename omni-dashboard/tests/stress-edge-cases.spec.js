import { test, expect } from '@playwright/test';

test.describe('Challenger Adversarial Stress & Edge Case Suite', () => {

  test.describe('Edge Case 1: Token Eviction Timing & Smooth Re-authentication', () => {
    test('clears token immediately on 401 and allows immediate successful re-login without page refresh', async ({ page }) => {
      // 1. Start with an expired token
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'expired-token-123');
      });

      // Initially /api/status returns 401
      let authFail = true;
      await page.route('**/api/status', async (route) => {
        if (authFail) {
          await route.fulfill({
            status: 401,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Invalid token' }),
          });
        } else {
          await route.fulfill({
            status: 200,
            json: { status: 'healthy', providers: {} },
          });
        }
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

      // Verify token is evicted immediately
      await expect.poll(async () => {
        return await page.evaluate(() => localStorage.getItem('omni_token'));
      }).toBeNull();

      // Verify login screen is displayed
      const passwordInput = page.locator('input[type="password"]');
      await expect(passwordInput).toBeVisible();

      // Now supply valid credentials and sign in immediately WITHOUT reloading the page
      authFail = false;
      await passwordInput.fill('fresh-valid-token');
      await page.click('button:has-text("Sign In")');

      // Verify token is updated and dashboard loads cleanly
      await expect.poll(async () => {
        return await page.evaluate(() => localStorage.getItem('omni_token'));
      }).toBe('fresh-valid-token');

      await expect(page.locator('h1:has-text("Overview")')).toBeVisible();
      await expect(page.locator('input[type="password"]')).not.toBeVisible();
    });

    test('evicts token and transitions cleanly on 403 Forbidden', async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'forbidden-token');
      });

      await page.route('**/api/status', async (route) => {
        await route.fulfill({
          status: 403,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Forbidden' }),
        });
      });
      await page.route('**/api/sessions', async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'Forbidden' } });
      });
      await page.route('**/api/models', async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'Forbidden' } });
      });

      await page.goto('/');

      await expect.poll(async () => {
        return await page.evaluate(() => localStorage.getItem('omni_token'));
      }).toBeNull();

      await expect(page.locator('input[type="password"]')).toBeVisible();
    });
  });

  test.describe('Edge Case 2: Multiple Concurrent Requests & Error Storms', () => {
    test('handles 5 simultaneous 401 responses gracefully without crashing or loops', async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'bad-token');
      });

      // All initial dashboard endpoints fail with 401 at the same time
      const endpoints = ['status', 'sessions', 'models', 'mcp/status', 'health'];
      for (const ep of endpoints) {
        await page.route(`**/api/${ep}`, async (route) => {
          await route.fulfill({
            status: 401,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Unauthorized' }),
          });
        });
      }

      await page.goto('/');

      // UI should cleanly stabilize on LoginScreen
      await expect(page.locator('input[type="password"]')).toBeVisible();
      await expect(page.locator('button:has-text("Sign In")')).toBeVisible();

      // Local storage must be clean
      const token = await page.evaluate(() => localStorage.getItem('omni_token'));
      expect(token).toBeNull();
    });

    test('rate limit toast storm does not exceed max toast queue capacity', async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'valid-token');
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
          json: { routing_policy: 'AUTO', sandbox_ttl_seconds: 300, sandbox_max_concurrent: 10 },
        });
      });

      await page.goto('/config');
      await expect(page.locator('h2:has-text("Configuration")')).toBeVisible();

      // Dispatch 10 rapid rate-limit events to simulate concurrent 429 storm
      await page.evaluate(() => {
        for (let i = 0; i < 10; i++) {
          window.dispatchEvent(
            new CustomEvent('api:rate-limited', {
              detail: { detail: `Rate limit burst ${i + 1}`, retryAfter: '30' },
            })
          );
        }
      });

      // ToastProvider caps previous toasts with .slice(-3) + new one = max 4 toasts
      const toastCount = await page.locator('div[role="alert"], div.fixed.bottom-4.right-4 > div').count();
      expect(toastCount).toBeLessThanOrEqual(4);
    });
  });

  test.describe('Edge Case 3: 429 Rate Limiting Details and Non-JSON Fallbacks', () => {
    test('renders Retry-After header cleanly in toast and handles HTML 429 from reverse proxies', async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'valid-token');
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
          json: { routing_policy: 'AUTO', sandbox_ttl_seconds: 300, sandbox_max_concurrent: 10 },
        });
      });

      // Mock a non-JSON HTML 429 (like Cloudflare or Nginx error page) with Retry-After
      await page.route('**/api/config/update', async (route) => {
        await route.fulfill({
          status: 429,
          contentType: 'text/html',
          headers: { 'Retry-After': '45' },
          body: '<html><body>429 Too Many Requests</body></html>',
        });
      });

      await page.goto('/config');
      await page.click('button:has-text("Save Changes")');

      // The fallback in api.js must parse Retry-After even if json() fails
      await expect(page.locator('text=Retry after 45s')).toBeVisible();

      // Session must remain intact
      const token = await page.evaluate(() => localStorage.getItem('omni_token'));
      expect(token).toBe('valid-token');
      await expect(page.locator('input[type="password"]')).not.toBeVisible();
    });
  });

  test.describe('Edge Case 4: Contract Verification on Config Update Types', () => {
    test('sends numbers as numbers and uppercase policy string', async ({ page }) => {
      await page.addInitScript(() => {
        window.localStorage.setItem('omni_token', 'valid-token');
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
          json: { routing_policy: 'AUTO', sandbox_ttl_seconds: 300, sandbox_max_concurrent: 10 },
        });
      });

      let parsedPayload = null;
      await page.route('**/api/config/update', async (route) => {
        parsedPayload = JSON.parse(route.request().postData());
        await route.fulfill({
          status: 200,
          json: { status: 'ok', updated: parsedPayload, errors: {} },
        });
      });

      await page.goto('/config');

      // Change policy and numbers
      await page.selectOption('select#routing_policy', 'ECO');
      await page.fill('input#sandbox_ttl_seconds', '600');
      await page.fill('input#sandbox_max_concurrent', '25');

      await page.click('button:has-text("Save Changes")');

      await expect(page.locator('text=Configuration updated successfully')).toBeVisible();

      expect(parsedPayload).toEqual({
        routing_policy: 'ECO',
        sandbox_ttl_seconds: 600,
        sandbox_max_concurrent: 25,
      });

      // Explicit type verification
      expect(typeof parsedPayload.routing_policy).toBe('string');
      expect(typeof parsedPayload.sandbox_ttl_seconds).toBe('number');
      expect(typeof parsedPayload.sandbox_max_concurrent).toBe('number');
    });
  });

});
