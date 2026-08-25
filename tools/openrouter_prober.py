"""
OpenRouter Free Model Prober — Background Task
Runs once at startup + every 12 hours. Validates live free models, removes
dead ones from the live config so the agent never hits 404 waterfalls.
"""
from __future__ import annotations
import asyncio
from core.logger import get_logger
from config import settings

log = get_logger(__name__)
_PROBE_INTERVAL_SECONDS = 12 * 60 * 60
_VALIDATED_FREE_MODELS: list[str] = []
_PROBE_LOCK = asyncio.Lock()


async def _fetch_free_models_from_api() -> list[str]:
    if not settings.openrouter_api_key:
        return []
    try:
        import httpx
        headers = {"Authorization": f"Bearer {settings.openrouter_api_key}",
                   "HTTP-Referer": "https://github.com/omniagent-homelab"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
        models = data.get("data", [])
        free = [m["id"] for m in models
                if ":free" in m.get("id", "") or
                str(m.get("pricing", {}).get("prompt", "1")) == "0"]
        log.info("OpenRouter probe: %d free models via API", len(free))
        return free
    except Exception as exc:
        log.warning("OpenRouter model fetch failed: %s", exc)
        return []


async def _validate_model(model_id: str) -> bool:
    if not settings.openrouter_api_key:
        return False
    try:
        import httpx
        headers = {"Authorization": f"Bearer {settings.openrouter_api_key}",
                   "HTTP-Referer": "https://github.com/omniagent-homelab",
                   "Content-Type": "application/json"}
        payload = {"model": model_id, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers, json=payload)
            return resp.status_code in (200, 429)
    except Exception:
        return False


async def probe_and_update_free_models() -> list[str]:
    async with _PROBE_LOCK:
        raw_free = await _fetch_free_models_from_api()
        if not raw_free:
            log.warning("OpenRouter prober: no free models from API, keeping existing list")
            return _VALIDATED_FREE_MODELS
        log.info("OpenRouter prober: validating %d candidates...", len(raw_free))
        semaphore = asyncio.Semaphore(5)
        async def _check(model_id: str) -> tuple[str, bool]:
            async with semaphore:
                ok = await _validate_model(model_id)
                return model_id, ok
        results = await asyncio.gather(*[_check(m) for m in raw_free])
        validated = [m for m, ok in results if ok]
        if not validated:
            log.warning("OpenRouter prober: zero models validated — keeping existing list")
            return _VALIDATED_FREE_MODELS
        log.info("OpenRouter prober: %d/%d live: %s", len(validated), len(raw_free),
                 ", ".join(validated[:5]) + ("..." if len(validated) > 5 else ""))
        _VALIDATED_FREE_MODELS.clear()
        _VALIDATED_FREE_MODELS.extend(validated)
        settings.openrouter_free_models = ",".join(validated)
        return validated


async def run_openrouter_prober_loop() -> None:
    """Long-running background coroutine. Probes at startup then every 12h."""
    await asyncio.sleep(30)
    while True:
        try:
            validated = await probe_and_update_free_models()
            log.info("OpenRouter prober cycle done: %d live free models", len(validated))
        except Exception as exc:
            log.error("OpenRouter prober cycle failed: %s", exc)
        await asyncio.sleep(_PROBE_INTERVAL_SECONDS)
