with open('config.py', 'r') as f:
    content = f.read()

injection = """
    # ── Logging ───────────────────────────────────────────────────────────────
    admin_api_secret: Optional[str] = Field(default=None)
"""
content = content.replace("    # ── Logging ───────────────────────────────────────────────────────────────", injection.strip("\n") + "\n    # ── Logging ───────────────────────────────────────────────────────────────")

with open('config.py', 'w') as f:
    f.write(content)
