import { test, expect } from '@playwright/test';

test.describe('Adversarial Resilience & Boundary Stress Suite', () => {
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
  });

  // =========================================================================
  // Objective 1: Config.jsx Boundary Validation (sandbox_ttl, max_concurrent, routing_policy)
  // =========================================================================
  test('Config.jsx: verifies HTML input boundaries and constraint validity', async ({ page }) => {
    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          routing_policy: 'AUTO',
          sandbox_ttl_seconds: 300,
          sandbox_max_concurrent: 5,
        },
      });
    });

    await page.goto('/config');
    await expect(page.locator('h2:has-text("Configuration")')).toBeVisible();

    const ttlInput = page.locator('input#sandbox_ttl_seconds');
    const concurrentInput = page.locator('input#sandbox_max_concurrent');
    const policySelect = page.locator('select#routing_policy');

    // 1. Boundary attributes
    await expect(ttlInput).toHaveAttribute('min', '60');
    await expect(ttlInput).toHaveAttribute('max', '3600');
    await expect(concurrentInput).toHaveAttribute('min', '1');
    await expect(concurrentInput).toHaveAttribute('max', '50');

    // 2. Policy select options
    const optionValues = await policySelect.locator('option').evaluateAll(opts => opts.map(o => o.value));
    expect(optionValues).toEqual(['AUTO', 'ECO', 'SPEED', 'QUALITY', 'OFFLINE']);

    // 3. Native constraint validation on out-of-boundary values
    // Lower boundary for TTL (< 60)
    await ttlInput.fill('59');
    const ttlUnderflow = await ttlInput.evaluate((el) => el.validity.rangeUnderflow);
    expect(ttlUnderflow).toBe(true);

    // Upper boundary for TTL (> 3600)
    await ttlInput.fill('3601');
    const ttlOverflow = await ttlInput.evaluate((el) => el.validity.rangeOverflow);
    expect(ttlOverflow).toBe(true);

    // Valid boundary for TTL
    await ttlInput.fill('60');
    expect(await ttlInput.evaluate((el) => el.checkValidity())).toBe(true);
    await ttlInput.fill('3600');
    expect(await ttlInput.evaluate((el) => el.checkValidity())).toBe(true);

    // Lower boundary for Concurrent (< 1)
    await concurrentInput.fill('0');
    const concUnderflow = await concurrentInput.evaluate((el) => el.validity.rangeUnderflow);
    expect(concUnderflow).toBe(true);

    // Upper boundary for Concurrent (> 50)
    await concurrentInput.fill('51');
    const concOverflow = await concurrentInput.evaluate((el) => el.validity.rangeOverflow);
    expect(concOverflow).toBe(true);

    // Valid boundary for Concurrent
    await concurrentInput.fill('1');
    expect(await concurrentInput.evaluate((el) => el.checkValidity())).toBe(true);
    await concurrentInput.fill('50');
    expect(await concurrentInput.evaluate((el) => el.checkValidity())).toBe(true);
  });

  test('Config.jsx: renders inline error messages and toast when backend returns validation errors', async ({ page }) => {
    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          routing_policy: 'AUTO',
          sandbox_ttl_seconds: 300,
          sandbox_max_concurrent: 5,
        },
      });
    });

    await page.route('**/api/config/update', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'no_change',
          updated: {},
          errors: {
            sandbox_ttl_seconds: 'Must be 60–3600',
            sandbox_max_concurrent: 'Must be 1–50',
            routing_policy: 'Must be one of: AUTO, ECO, SPEED, QUALITY, OFFLINE',
          },
        }),
      });
    });

    await page.goto('/config');
    await expect(page.locator('h2:has-text("Configuration")')).toBeVisible();

    // Trigger save with initial valid numbers to pass HTML5 check and reach backend mock
    await page.click('button:has-text("Save Changes")');

    // Verify inline error alerts rendered below each field
    await expect(page.locator('text=Must be 60–3600')).toBeVisible();
    await expect(page.locator('text=Must be 1–50')).toBeVisible();
    await expect(page.locator('text=Must be one of: AUTO, ECO, SPEED, QUALITY, OFFLINE')).toBeVisible();
    await expect(page.locator('text=Configuration validation failed')).toBeVisible();
  });

  test('Config.jsx: handles partial update status with warnings and invalidation', async ({ page }) => {
    await page.route('**/api/config', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          routing_policy: 'AUTO',
          sandbox_ttl_seconds: 300,
          sandbox_max_concurrent: 5,
        },
      });
    });

    await page.route('**/api/config/update', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'partial',
          updated: { routing_policy: 'QUALITY' },
          errors: {
            sandbox_ttl_seconds: 'Must be 60–3600',
          },
        }),
      });
    });

    await page.goto('/config');
    await page.click('button:has-text("Save Changes")');

    await expect(page.locator('text=Configuration updated with partial warnings')).toBeVisible();
    await expect(page.locator('text=Must be 60–3600')).toBeVisible();
  });

  // =========================================================================
  // Objective 2: McpStatusCard.jsx Defensive Exception Handling
  // =========================================================================
  test('McpStatusCard.jsx: defensively handles runtime exception payload { available: false, servers: {}, error: "..." }', async ({ page }) => {
    const runtimeErrorMsg = 'MCP daemon socket timeout: /tmp/mcp.sock unreachable';

    await page.route('**/api/mcp/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          available: false,
          servers: {},
          error: runtimeErrorMsg,
        }),
      });
    });

    await page.goto('/');
    await expect(page.locator('h1:has-text("Overview")')).toBeVisible();

    // Verify McpStatusCard displays Degraded status badge
    await expect(page.locator('text=Status Degraded')).toBeVisible();

    // Verify Warning banner is shown with exact error message
    await expect(page.locator('text=MCP Subsystem Warning')).toBeVisible();
    await expect(page.locator(`text=${runtimeErrorMsg}`)).toBeVisible();

    // Verify total tools count displays 0 Tools
    await expect(page.locator('text=0 Tools')).toBeVisible();
  });

  test('McpStatusCard.jsx: displays circuit breaker tripped state and server tools', async ({ page }) => {
    await page.route('**/api/mcp/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          available: true,
          total_tools: 3,
          servers: {
            sqlite: {
              healthy: false,
              disabled: true,
              failures: 4,
              tools_count: 3,
              tool_names: ['query_db', 'list_tables', 'schema_inspect'],
            },
          },
        }),
      });
    });

    await page.goto('/');
    await expect(page.locator('h1:has-text("Overview")')).toBeVisible();

    // Verify circuit breaker tripped badge and details
    await expect(page.locator('text=Circuit Breaker Tripped')).toBeVisible();
    await expect(page.locator('text=query_db')).toBeVisible();
    await expect(page.locator('text=Failures:')).toBeVisible();
  });

  // =========================================================================
  // Objective 3: Models.jsx Fallback Handling
  // =========================================================================
  test('Models.jsx: handles runtime exception on /api/models/status with fallback scoring', async ({ page }) => {
    await page.route('**/api/models', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          models: [
            {
              id: 'anthropic/claude-3-5-sonnet',
              provider: 'anthropic',
              intelligence: 9,
              speed: 8,
              tool_reliability: 9,
              vision: true,
              available: true,
            },
          ],
        },
      });
    });

    await page.route('**/api/models/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'Pareto computation engine failed: Division by zero',
          models: [],
        }),
      });
    });

    await page.goto('/models');
    await expect(page.locator('h2:has-text("Model Registry")')).toBeVisible();

    // Verify fallback badge is shown
    await expect(page.locator('text=Fallback Scoring')).toBeVisible();

    // Verify model is rendered from catalogue (truncated display or title)
    const modelCell = page.locator('span[title="anthropic/claude-3-5-sonnet"]');
    await expect(modelCell).toBeVisible();
    await expect(modelCell).toHaveText('anthropic/claude-3-5...');

    // Heuristic score: (9*3 + 8 + 9*2) = 27 + 8 + 18 = 53
    await expect(page.locator('text=53')).toBeVisible();

    // Verify rank is '-'
    await expect(page.getByRole('cell', { name: '-', exact: true })).toBeVisible();
  });

  test('Models.jsx: handles empty array on both models and status without crashing', async ({ page }) => {
    await page.route('**/api/models', async (route) => {
      await route.fulfill({ status: 200, json: { models: [] } });
    });
    await page.route('**/api/models/status', async (route) => {
      await route.fulfill({ status: 200, json: { models: [], total: 0 } });
    });

    await page.goto('/models');
    await expect(page.locator('h2:has-text("Model Registry")')).toBeVisible();

    // Verify empty state display
    await expect(page.locator('text=No models found.')).toBeVisible();
    await expect(page.locator('text=0 Total')).toBeVisible();
  });
});
