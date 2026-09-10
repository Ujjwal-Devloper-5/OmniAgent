"""
Browser Automation Tool — OmniAgent Phase 4
════════════════════════════════════════════
Headless Chromium browser automation via Playwright.
Runs INSIDE the sandbox container — fully isolated.

The browser agent can:
  - Navigate to URLs
  - Click elements (by text, CSS selector, or XPath)
  - Fill forms
  - Take screenshots (saved to /workspace/)
  - Extract page text/HTML
  - Evaluate JavaScript
  - Handle SPAs (waits for network idle)

First-use: installs Playwright + Chromium inside the sandbox (~500MB, once per session).
"""

import base64
import hashlib
from langchain_core.tools import tool
from tools.sandbox_tool import _get_pool

_PLAYWRIGHT_INSTALLED: set[str] = set()

@tool
async def browser_act(
    action: str,
    session_id: str = "default",
    url: str = "",
    selector: str = "",
    text: str = "",
    screenshot_name: str = "screenshot.png",
    js_code: str = "",
    timeout_ms: int = 30000,
) -> str:
    """
    Control a headless Chromium browser inside the sandbox.
    
    Args:
        action:          What to do: 'navigate', 'click', 'type', 'screenshot',
                         'get_text', 'get_html', 'evaluate_js', 'wait'
        session_id:      Session identifier (browser state persists within session)
        url:             URL to navigate to (for 'navigate' action)
        selector:        CSS selector or text content to target element
                         (for 'click', 'type' actions)
        text:            Text to type into element (for 'type' action)
        screenshot_name: Filename for screenshot (default: 'screenshot.png')
        js_code:         JavaScript to evaluate in page context (for 'evaluate_js')
        timeout_ms:      Timeout in milliseconds (default: 30000)
    
    Returns:
        Action result — page text, screenshot confirmation, element content, or JS result.
    
    Actions:
        'navigate'    — Go to URL, wait for page load. Returns page title.
        'click'       — Click element matching selector or text.
        'type'        — Type text into element matching selector.
        'screenshot'  — Take full-page screenshot saved to /workspace/{screenshot_name}
        'get_text'    — Extract all visible text from current page
        'get_html'    — Get page HTML source (truncated to 10KB)
        'evaluate_js' — Run JavaScript in page context and return result
        'wait'        — Wait for element matching selector to appear
    
    Examples:
        browser_act('navigate', url='https://example.com')
        browser_act('screenshot', screenshot_name='page.png')
        browser_act('get_text')
        browser_act('click', selector='button[type=submit]')
        browser_act('type', selector='input[name=email]', text='test@example.com')
    """
    pool = _get_pool()
    session_key = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    
    try:
        container_id = await pool.get_or_create(session_key)
    except Exception as e:
        return f"❌ Sandbox error: {e}"

    # Install playwright once per container
    if session_key not in _PLAYWRIGHT_INSTALLED:
        install_cmd = (
            "pip install playwright --quiet 2>/dev/null && "
            "playwright install chromium --with-deps 2>/dev/null && "
            "echo 'Browser ready'"
        )
        await pool.exec_in(container_id, install_cmd, timeout=300)
        _PLAYWRIGHT_INSTALLED.add(session_key)

    state_file = f"/tmp/browser_state_{session_key}.json"

    # Define action specifics
    if action == 'navigate':
        action_code = f"await page.goto('{url}', timeout={timeout_ms}, wait_until='networkidle')\\n    result = await page.title()"
    elif action == 'click':
        action_code = f"await page.locator('{selector}').first.click(timeout={timeout_ms})\\n    result = 'Clicked {selector}'"
    elif action == 'type':
        action_code = f"await page.locator('{selector}').first.fill('{text}', timeout={timeout_ms})\\n    result = 'Typed text into {selector}'"
    elif action == 'screenshot':
        action_code = f"await page.screenshot(path='/workspace/{screenshot_name}', full_page=True, timeout={timeout_ms})\\n    result = 'Screenshot saved to /workspace/{screenshot_name}'"
    elif action == 'get_text':
        action_code = "result = await page.evaluate('document.body.innerText')"
    elif action == 'get_html':
        action_code = "result = await page.content()\\n    result = result[:10000]"
    elif action == 'evaluate_js':
        js_b64 = base64.b64encode(js_code.encode()).decode()
        action_code = f"import base64\\n    decoded_js = base64.b64decode('{js_b64}').decode()\\n    result = await page.evaluate(decoded_js)"
    elif action == 'wait':
        action_code = f"await page.locator('{selector}').first.wait_for(timeout={timeout_ms})\\n    result = 'Element {selector} appeared'"
    else:
        return f"❌ Unknown action '{action}'"

    script = f'''import asyncio
import os
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        
        context_args = {{}}
        state_file = '{state_file}'
        if os.path.exists(state_file):
            context_args['storage_state'] = state_file
            
        context = await browser.new_context(**context_args)
        
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        
        result = ""
        try:
            {action_code}
        except Exception as e:
            result = f"Error: {{str(e)}}"
            
        await context.storage_state(path=state_file)
        await browser.close()
        print(result)

asyncio.run(main())
'''
    
    script_b64 = base64.b64encode(script.encode()).decode()
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{script_b64}').decode())\""
    
    stdout, stderr, exit_code = await pool.exec_in(container_id, cmd, timeout=(timeout_ms//1000) + 15)
    
    if exit_code == 0:
        return f"✅ Browser action '{action}' succeeded:\n{stdout.strip()}"
    else:
        return f"❌ Browser action '{action}' failed (exit {exit_code}):\n{stderr.strip()}"
