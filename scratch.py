import sys

with open('/home/ujjwal/homelab/aibot/core/model_router.py', 'r') as f:
    content = f.read()

# 2.2 Fix _PROVIDER_CAPS
import re

new_func = '''
def _get_provider_caps(provider: ModelProvider) -> set[str]:
    """
    Derive a provider's capabilities dynamically from the tags of all its
    available models in the registry. Falls back to a safe minimal set if
    the registry is empty or not yet initialised.
    """
    try:
        reg = get_registry()
        caps: set[str] = set()
        for model in reg._models.values():
            if model.provider == provider.value and model.is_available:
                caps.update(model.tags)
        if caps:
            return caps
    except Exception:
        pass
    # Fallback static minimums (safe, never empty)
    return _PROVIDER_CAPS_FALLBACK.get(provider, {"text", "general"})

_PROVIDER_CAPS_FALLBACK: dict[ModelProvider, set[str]] = {
'''

content = content.replace('_PROVIDER_CAPS: dict[ModelProvider, set[str]] = {', new_func)

content = content.replace('_PROVIDER_CAPS[ModelProvider.OLLAMA] |=', '_PROVIDER_CAPS_FALLBACK[ModelProvider.OLLAMA] |=')
content = content.replace('_PROVIDER_CAPS.get(p, set())', '_get_provider_caps(p)')
content = content.replace('_PROVIDER_CAPS.get(provider, set())', '_get_provider_caps(provider)')


# 2.3 _select_best_model_for_task

old_select_best = '''    def _select_best_model_for_task(
        self,
        task_type: TaskType,
        available: list[ModelProvider],
        needs_vision: bool = False,
        needs_tools: bool = False,
    ) -> list[ModelProvider]:
        """
        Build an ordered provider list using the dynamic ModelRegistry.

        Queries the registry for all eligible models scored by
        intelligence/speed/tool_reliability, then maps each model's provider
        to the agents dict.  Models from unavailable providers are skipped.

        Falls back to ``_build_priority_list`` (legacy _TASK_PREFERENCES) if
        the registry returns no candidates — so the system degrades gracefully
        even if models.json is missing or empty.

        Parameters
        ----------
        task_type    : Classified task (CODING, MATH, VISION, etc.)
        available    : Providers confirmed available at boot probe.
        needs_vision : Whether the request includes media / requires vision.
        needs_tools  : Whether tool-calling reliability should be weighted.
        """
        try:
            registry = get_registry()
            ranked = registry.get_ranked_list(
                task_type=task_type.value,
                needs_vision=needs_vision,
                needs_tools=needs_tools,
            )
        except Exception as exc:
            log.warning(
                "_select_best_model_for_task: registry error (%s) — using legacy list",
                exc,
            )
            return self._build_priority_list(task_type, available, needs_vision)

        if not ranked:
            log.debug(
                "_select_best_model_for_task: registry empty for task=%s — using legacy list",
                task_type.value,
            )
            return self._build_priority_list(task_type, available, needs_vision)

        # Map scored models → provider enum, deduplicate, skip unavailable
        seen: set[ModelProvider] = set()
        ordered: list[ModelProvider] = []
        for model_entry in ranked:
            try:
                provider = ModelProvider(model_entry.provider)
            except ValueError:
                continue  # Unknown provider in registry entry — skip
            if provider not in available or provider in seen:
                continue
            seen.add(provider)
            ordered.append(provider)

        if not ordered:
            # All registry-recommended providers are unavailable right now
            log.debug(
                "_select_best_model_for_task: all registry providers unavailable — using legacy list",
            )
            return self._build_priority_list(task_type, available, needs_vision)

        # Append any remaining available providers not yet in list (full fallback chain)
        for p in self._build_priority_list(task_type, available, needs_vision):
            if p not in seen:
                ordered.append(p)

        log.debug(
            "_select_best_model_for_task | task=%s → %s",
            task_type.value,
            [p.value for p ordered],
        )
        return ordered'''

new_select_best = '''    def _select_best_model_for_task(
        self,
        task_type: TaskType,
        available: list[ModelProvider],
        needs_vision: bool = False,
        needs_tools: bool = False,
        routing_policy: str = "AUTO",
    ) -> list[tuple[ModelProvider, str | None]]:
        """
        Returns ordered list of (provider, model_id) tuples.
        model_id is the registry-selected best model for that provider,
        or None if the provider should use its own default selection.
        """
        try:
            registry = get_registry()
            ranked = registry.get_ranked_list(
                task_type=task_type.value,
                needs_vision=needs_vision,
                needs_tools=needs_tools,
                routing_policy=routing_policy,
            )
        except Exception as exc:
            log.warning("_select_best_model_for_task: registry error (%s) — using legacy list", exc)
            return [(p, None) for p in self._build_priority_list(task_type, available, needs_vision)]

        if not ranked:
            return [(p, None) for p in self._build_priority_list(task_type, available, needs_vision)]

        # Build (provider, model_id) list — one model_id per provider (best scoring)
        seen: set[ModelProvider] = set()
        ordered: list[tuple[ModelProvider, str | None]] = []
        for model_entry in ranked:
            try:
                provider = ModelProvider(model_entry.provider)
            except ValueError:
                continue
            if provider not in available or provider in seen:
                continue
            seen.add(provider)
            ordered.append((provider, model_entry.id))  # ← preserve model ID!

        if not ordered:
            return [(p, None) for p in self._build_priority_list(task_type, available, needs_vision)]

        # Append remaining available providers not covered by registry
        for p in self._build_priority_list(task_type, available, needs_vision):
            if p not in seen:
                ordered.append((p, None))
                seen.add(p)

        log.debug(
            "_select_best_model_for_task | task=%s → %s",
            task_type.value,
            [(p.value, m) for p, m in ordered],
        )
        return ordered'''


# Let's use regex to replace it because formatting might not exactly match
import re
content = re.sub(
    r'    def _select_best_model_for_task\([^:]+?\) -> list\[ModelProvider\]:.*?return ordered', 
    new_select_best, 
    content, flags=re.DOTALL)


# Update route()
# Read routing_policy at the top of the method
route_def = """    async def route(
        self,
        session_id:     str,
        message:        str,
        platform:       str = "unknown",
        force_provider: Optional[ModelProvider] = None,
        has_media:      bool = False,
        image_data:     bytes | None = None,
        image_mime:     str = "image/jpeg",
    ) -> AgentResponse:
        \"\"\""""
        
route_def_new = """    async def route(
        self,
        session_id:     str,
        message:        str,
        platform:       str = "unknown",
        force_provider: Optional[ModelProvider] = None,
        has_media:      bool = False,
        image_data:     bytes | None = None,
        image_mime:     str = "image/jpeg",
    ) -> AgentResponse:
        \"\"\"
        routing_policy = self._settings.routing_policy"""

content = content.replace(route_def, route_def_new)

# Guard for offline policy
offline_guard = """
        needs_vision = has_media or task_type == TaskType.VISION
        available = self._get_available_providers()
        
        if routing_policy == "OFFLINE":
            available = [p for p in available if p == ModelProvider.OLLAMA]
            if not available:
                raise RuntimeError(
                    "ROUTING_POLICY=OFFLINE but Ollama is not available. "
                    "Start Ollama (ollama serve) and ensure models are pulled."
                )

        # Capability filtering"""

content = content.replace("""        needs_vision = has_media or task_type == TaskType.VISION
        available = self._get_available_providers()
        
        # Capability filtering""", offline_guard)


# Route method priority building
old_force = """        if force_provider:
            if force_provider in available:
                priority = [force_provider]
                for p in self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True
                ):
                    if p not in priority:
                        priority.append(p)
            else:
                log.warning(
                    "Forced provider %s not available, using auto-routing",
                    force_provider.value,
                )
                priority = self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True
                )
        else:
            # Registry-first: scored dynamic pool, falls back to _TASK_PREFERENCES
            priority = self._select_best_model_for_task(
                task_type, available, needs_vision, needs_tools=True
            )"""
            
new_force = """        if force_provider:
            if force_provider in available:
                priority = [(force_provider, None)]
                for p, m_id in self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True, routing_policy=routing_policy
                ):
                    if not any(x[0] == p for x in priority):
                        priority.append((p, m_id))
            else:
                log.warning(
                    "Forced provider %s not available, using auto-routing",
                    force_provider.value,
                )
                priority = self._select_best_model_for_task(
                    task_type, available, needs_vision, needs_tools=True, routing_policy=routing_policy
                )
        else:
            # Registry-first: scored dynamic pool, falls back to _TASK_PREFERENCES
            priority = self._select_best_model_for_task(
                task_type, available, needs_vision, needs_tools=True, routing_policy=routing_policy
            )"""
            
content = content.replace(old_force, new_force)


# Route process_message loop

old_loop = """        first_choice = priority[0]
        last_error: Exception | None = None

        # Platform-aware system suffix (not stored in memory)
        char_limit = _PLATFORM_LIMITS.get(platform)
        platform_system_note = (
            f"\\n\\n[PLATFORM CONSTRAINT — {platform.upper()}]: "
            f"Keep your response under {char_limit} characters. "
            f"Be concise. If showing code, keep it short but complete."
            if char_limit else ""
        )

        for i, provider in enumerate(priority):
            agent = self._agents[provider]
            is_fallback = (i > 0)

            # Determine if this specific provider can handle vision
            provider_caps = _get_provider_caps(provider)
            effective_vision = needs_vision and bool(provider_caps & _VISION_CAPABLE)

            log.info(
                "Trying provider=%s task=%s vision=%s (%d/%d)%s",
                provider.value, task_type.value, effective_vision,
                i + 1, len(priority),
                " [FALLBACK]" if is_fallback else "",
            )

            try:
                response = await agent.process_message(
                    session_id=session_id,
                    message=message,
                    platform=platform,
                    task_type=task_type,
                    platform_system_note=platform_system_note,
                    needs_vision=effective_vision,
                    image_data=image_data if effective_vision else None,
                    image_mime=image_mime,
                )"""

new_loop = """        first_choice = priority[0][0]
        last_error: Exception | None = None

        # Platform-aware system suffix (not stored in memory)
        char_limit = _PLATFORM_LIMITS.get(platform)
        platform_system_note = (
            f"\\n\\n[PLATFORM CONSTRAINT — {platform.upper()}]: "
            f"Keep your response under {char_limit} characters. "
            f"Be concise. If showing code, keep it short but complete."
            if char_limit else ""
        )

        for i, (provider, preferred_model_id) in enumerate(priority):
            agent = self._agents[provider]
            is_fallback = (i > 0)

            # Determine if this specific provider can handle vision
            provider_caps = _get_provider_caps(provider)
            effective_vision = needs_vision and bool(provider_caps & _VISION_CAPABLE)

            log.info(
                "Trying provider=%s task=%s vision=%s (%d/%d)%s",
                provider.value, task_type.value, effective_vision,
                i + 1, len(priority),
                " [FALLBACK]" if is_fallback else "",
            )

            try:
                response = await agent.process_message(
                    session_id=session_id,
                    message=message,
                    platform=platform,
                    task_type=task_type,
                    platform_system_note=platform_system_note,
                    needs_vision=effective_vision,
                    image_data=image_data if effective_vision else None,
                    image_mime=image_mime,
                    preferred_model=preferred_model_id,
                )"""

content = content.replace(old_loop, new_loop)

# Two-stage fallback in exception block
old_except = """            except Exception as exc:
                log.error("✗ Provider %s failed: %s", provider.value, exc)
                self._record_failure(provider)
                # Inform registry so it can score-demote the specific model
                try:
                    # Best effort: find the model_id for this provider from registry
                    reg = get_registry()
                    ranked = reg.get_ranked_list(task_type.value, needs_vision, True)
                    for m in ranked:
                        if m.provider == provider.value:
                            reg.record_failure(m.id)
                            break
                except Exception:
                    pass
                last_error = exc
                continue"""

new_except = """            except Exception as exc:
                log.error("✗ Provider %s failed: %s", provider.value, exc)
                self._record_failure(provider)
                
                # Record failure at model level too, so the specific model is demoted
                # while other models from the same provider may still be tried
                try:
                    get_registry().record_failure(preferred_model_id)
                except Exception:
                    pass
                    
                # Inform registry so it can score-demote the specific model
                try:
                    # Best effort: find the model_id for this provider from registry
                    reg = get_registry()
                    ranked = reg.get_ranked_list(task_type.value, needs_vision, True, routing_policy=routing_policy)
                    for m in ranked:
                        if m.provider == provider.value:
                            reg.record_failure(m.id)
                            break
                except Exception:
                    pass
                last_error = exc
                continue"""
                
content = content.replace(old_except, new_except)

# And raise error at the end
content = content.replace("f\"Tried: {[p.value for p in priority]}\"", "f\"Tried: {[p[0].value for p, _ in priority]}\"")

with open('/home/ujjwal/homelab/aibot/core/model_router.py', 'w') as f:
    f.write(content)
