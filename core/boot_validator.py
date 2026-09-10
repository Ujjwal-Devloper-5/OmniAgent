"""
Pre-Flight Safety Validator

Ensures all critical configurations are present before the application initializes.
Fails fast with clear terminal errors to prevent downstream tracebacks.
"""

import sys
from config import settings
from core.logger import get_logger

log = get_logger(__name__)

def validate_environment() -> None:
    """Run environmental safety checks and exit the process if validation fails."""
    errors = []

    # 1. AI Provider Validation
    # Ensure at least one LLM provider is configured for operation.
    has_gemini = bool(settings.gemini_api_key)
    has_openrouter = bool(settings.openrouter_api_key)
    has_groq = bool(settings.groq_api_key)
    has_openai = bool(settings.openai_api_key)
    has_anthropic = bool(settings.anthropic_api_key)
    has_ollama = bool(settings.ollama_base_url)

    if not any([has_gemini, has_openrouter, has_groq, has_openai, has_anthropic, has_ollama]):
        errors.append("ERROR: No AI provider configured. Set at least one API key (e.g., GEMINI_API_KEY, GROQ_API_KEY) or Ollama base URL in your .env file.")

    # 2. Swarm Limits Validation
    # Enforce upper bound on dynamic agent limits to prevent resource exhaustion.
    if settings.swarm_max_dynamic_agents > 10:
        log.warning("swarm_max_dynamic_agents is set to %d. Hard-capping to 10 for resource safety.", settings.swarm_max_dynamic_agents)
        settings.swarm_max_dynamic_agents = 10
        
    # 3. Platform Configuration Validation
    # Ensure at least one communication platform is enabled.
    if not settings.discord_token and not settings.telegram_token and not settings.slack_bot_token:
        errors.append("ERROR: No platform tokens configured. Set DISCORD_TOKEN, TELEGRAM_TOKEN, or SLACK_BOT_TOKEN in your .env file.")

    if errors:
        print("\n" + "=" * 70)
        print(" OMNIAGENT STARTUP ABORTED")
        print("=" * 70)
        for err in errors:
            print(f" {err}")
        print("=" * 70)
        print(" Please update your configuration and restart the application.\n")
        sys.exit(1)
        
    log.info("Environment validation successful. Boot sequence continuing.")
