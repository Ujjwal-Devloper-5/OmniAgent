import os
import glob

# 1. Update base.py
with open("core/agents/base.py", "r") as f:
    content = f.read()

content = content.replace(
    'def build_system_prompt(platform_note: str = "") -> str:',
    'async def build_system_prompt(platform_note: str = "", session_id: str = "") -> str:'
)

content = content.replace(
    'if platform_note:\n        prompt += f"\\n{platform_note}"\n    return prompt',
    """if platform_note:
        prompt += f"\\n{platform_note}"
    
    # ── Per-user custom system prompt override (from Admin Dashboard) ──────────
    try:
        if session_id:
            from core.user_settings import get_user_settings
            user_settings = await get_user_settings().get(session_id)
            custom_prompt = user_settings.get("system_prompt")
            if custom_prompt and custom_prompt.strip():
                prompt += f"\\n\\n[CUSTOM SYSTEM PROMPT FOR THIS USER]\\n{custom_prompt.strip()}"
    except Exception:
        pass  # Non-fatal
    
    return prompt"""
)

# Remove the synchronous call for SHARED_SYSTEM_PROMPT or mock it
content = content.replace(
    'SHARED_SYSTEM_PROMPT = build_system_prompt()',
    'SHARED_SYSTEM_PROMPT = ""  # Replaced by async call'
)

with open("core/agents/base.py", "w") as f:
    f.write(content)

# 2. Update all agent backends
agent_files = glob.glob("core/agents/*_agent.py")
for f_name in agent_files:
    with open(f_name, "r") as f:
        agent_content = f.read()
    
    # Replace the synchronous call with an await call and add session_id
    agent_content = agent_content.replace(
        'build_system_prompt(platform_system_note)',
        'await build_system_prompt(platform_system_note, session_id)'
    )
    # Also in case it passes it without args
    agent_content = agent_content.replace(
        'build_system_prompt()',
        'await build_system_prompt(session_id=session_id)'
    )
    
    with open(f_name, "w") as f:
        f.write(agent_content)
