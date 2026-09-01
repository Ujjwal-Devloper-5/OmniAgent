with open('main.py', 'r') as f:
    content = f.read()

injection = """
    tasks: list[asyncio.Task] = []

    # ── Admin API ────────────────────────────────────────────────────────────────
    from adapters.admin_api import start_admin_api
    tasks.append(asyncio.create_task(start_admin_api(), name="admin_api"))
"""
content = content.replace("    tasks: list[asyncio.Task] = []", injection.strip("\n"))

with open('main.py', 'w') as f:
    f.write(content)
