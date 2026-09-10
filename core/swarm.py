"""
OmniAgent Swarm — Professional multi-agent orchestration.

Architecture:
  SwarmSupervisor
  ├── Plans which specialist agents to call (Research, Coder, Writer, Analyst)
  ├── Calls them sequentially, passing prior work as context
  ├── Synthesizes all outputs into one final response
  └── Hard limits: MAX_STEPS=5, TIMEOUT=90s — no infinite loops ever

Design principles:
  - Context isolation: each sub-agent gets its own session ID (no polluting main context)
  - Structured handoff: each agent writes to a shared scratchpad dict
  - Failure isolation: if one sub-agent fails, supervisor continues with remaining agents
  - Budget enforcement: steps and wall-clock time are both capped
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from core.logger import get_logger
from config import settings

log = get_logger(__name__)

AGENT_CHAR_CAP = 8000  # max chars per sub-agent output before synthesis


# ─────────────────────────────────────────────────────────────────────────────
# Swarm Role → Model Preference Mapping
# Maps role keywords to routing hints for model selection
# ─────────────────────────────────────────────────────────────────────────────

# Maps lowercase role name substrings → (routing_policy, task_type_hint)
# routing_policy: which policy to use when routing this role's messages
# task_type_hint: preferred TaskType for this role
_ROLE_ROUTING: list[tuple[str, str, str]] = [
    # (role_keyword_substring, routing_policy, task_type_hint)
    ("research",     "SPEED",   "research"),   # Groq LPU → fast gathering
    ("scrape",       "SPEED",   "research"),
    ("gather",       "SPEED",   "research"),
    ("coder",        "AUTO",    "coding"),      # Registry picks best coding model
    ("developer",    "AUTO",    "coding"),
    ("programmer",   "AUTO",    "coding"),
    ("code",         "AUTO",    "coding"),
    ("qa",           "QUALITY", "analysis"),   # Best available model for review
    ("reviewer",     "QUALITY", "analysis"),
    ("validator",    "QUALITY", "analysis"),
    ("analyst",      "QUALITY", "analysis"),
    ("writer",       "AUTO",    "creative"),
    ("synthesizer",  "SPEED",   "general"),    # Final merge — speed matters
    ("orchestrator", "QUALITY", "analysis"),
]


def _get_role_routing(role: str) -> tuple[str, str]:
    """
    Given a swarm role name, return (routing_policy, task_type_hint).
    Matches by substring of the role name (case-insensitive).
    Defaults to AUTO/general if no match found.
    """
    role_lower = role.lower()
    for keyword, policy, task_hint in _ROLE_ROUTING:
        if keyword in role_lower:
            return policy, task_hint
    return "AUTO", "general"


@dataclass
class SwarmContext:
    """Shared scratchpad passed between swarm agents."""
    original_query: str
    session_id: str
    platform: str
    scratchpad: dict[str, str] = field(default_factory=dict)
    generated_files: list[str] = field(default_factory=list)
    steps_taken: int = 0
    start_time: float = field(default_factory=time.time)

    def budget_remaining(self) -> float:
        return settings.swarm_total_timeout_seconds - (time.time() - self.start_time)

    def is_over_budget(self) -> bool:
        return self.steps_taken >= settings.swarm_max_steps or self.budget_remaining() < 5.0


# Dynamic Orchestrator Prompt
_ORCHESTRATOR_PROMPT = """You are the OmniAgent Swarm Orchestrator. 
Your job is to break down the USER REQUEST into a sequential plan of highly specialized AI agent roles.
Each agent will run one after the other, passing their findings down the chain.

CRITICAL RULES:
1. You MUST output ONLY a valid JSON array. Do not include markdown formatting or conversational text.
2. The JSON array must contain objects with the following keys:
   - "role" (string)
   - "instructions" (string)
   - "requires_qa" (boolean) - set to true if this step is complex and requires rigorous testing/review.
   - "qa_instructions" (string, optional) - specific validation criteria if requires_qa is true.

Example Output:
[
  {
    "role": "DataScraper",
    "instructions": "Use web_search to find recent US Treasury bond holder data. Extract exact figures.",
    "requires_qa": false
  },
  {
    "role": "PythonDeveloper",
    "instructions": "Write python code to generate a PDF chart of the data provided by DataScraper.",
    "requires_qa": true,
    "qa_instructions": "Verify the code executes without errors and generates a valid PDF. Reject if the PDF is missing or broken."
  }
]
"""


class SwarmSupervisor:
    """
    Orchestrates specialist agents to handle complex multi-step tasks.
    Called when the main router detects a swarm-worthy request.
    """

    async def run(self, query: str, session_id: str, platform: str) -> str:
        ctx = SwarmContext(
            original_query=query,
            session_id=session_id,
            platform=platform,
        )

        from core.agent import process_message as _proc
        from tools.upload_tool import get_upload_context, set_upload_context
        import json
        import re

        # Suppress platform uploads while internal agents are iterating
        original_upload_ctx = get_upload_context()
        if original_upload_ctx:
            set_upload_context(original_upload_ctx.platform, original_upload_ctx.target_id, is_internal_swarm=True)

        # 1. Ask Orchestrator for the plan
        orchestrator_prompt = f"{_ORCHESTRATOR_PROMPT}\n\nUSER REQUEST: {query}"
        try:
            plan_json_str = await _proc(session_id + ":swarm:orchestrator", orchestrator_prompt, platform=platform)
            # Extract JSON array
            match = re.search(r'\[.*\]', plan_json_str, re.DOTALL)
            raw_json = match.group(0) if match else plan_json_str
            plan = json.loads(raw_json)
            
            # Enforce dynamic agent limits for safety and billing control
            if len(plan) > settings.swarm_max_dynamic_agents:
                log.warning("Orchestrator requested %d agents. Capping at %d.", len(plan), settings.swarm_max_dynamic_agents)
                plan = plan[:settings.swarm_max_dynamic_agents]
        except Exception as e:
            log.error("Orchestrator failed to generate valid JSON plan: %s", e)
            plan = [{"role": "Generalist", "instructions": "Complete the user request."}]

        log.info("Swarm started | session=%s | dynamic_agents=%d", session_id, len(plan))

        for agent_def in plan:
            agent_name = agent_def.get("role", "Specialist").replace(" ", "")
            base_instructions = agent_def.get("instructions", "Assist the user.")
            requires_qa = agent_def.get("requires_qa", False)
            qa_instructions = agent_def.get("qa_instructions", "Verify output is correct.")

            # Determine role-appropriate routing policy and task type
            role_policy, role_task_type = _get_role_routing(agent_name)
            log.info(
                "Swarm role=%s → policy=%s task=%s",
                agent_name, role_policy, role_task_type,
            )

            max_retries = 3 if requires_qa else 1
            feedback_context = ""

            for attempt in range(max_retries):
                files_checkpoint = len(ctx.generated_files)
                if ctx.is_over_budget():
                    log.warning(
                        "Swarm budget exhausted | session=%s | steps=%d | elapsed=%.1fs",
                        session_id, ctx.steps_taken, time.time() - ctx.start_time
                    )
                    break

                try:
                    current_instructions = f"{base_instructions}\n\n{feedback_context}" if feedback_context else base_instructions
                    result = await asyncio.wait_for(
                        self._run_agent(agent_name, current_instructions, ctx, routing_policy=role_policy),
                        timeout=min(settings.swarm_agent_timeout_seconds, max(10.0, ctx.budget_remaining() - 5.0)),
                    )
                    ctx.scratchpad[agent_name] = result[:AGENT_CHAR_CAP]
                    ctx.steps_taken += 1
                    
                    # Intercept any generated files that were suppressed from upload
                    draft_matches = re.findall(r'\[INTERNAL_DRAFT_READY:\s*(.*?)\]', result)
                    for file_path in draft_matches:
                        if file_path not in ctx.generated_files:
                            ctx.generated_files.append(file_path)

                    log.info(
                        "Swarm step %d/%d complete | agent=%s | attempt=%d/%d | output_len=%d",
                        ctx.steps_taken, settings.swarm_max_steps, agent_name, attempt + 1, max_retries, len(result),
                    )

                    if not requires_qa or attempt == max_retries - 1:
                        break

                    # Execute QA validation
                    qa_prompt = (
                        f"You are a strict QA Reviewer. Evaluate the following work output against these criteria.\n\n"
                        f"QA CRITERIA:\n{qa_instructions}\n\n"
                        f"WORK OUTPUT TO EVALUATE:\n{result}\n\n"
                        f"RESPOND with your analysis first, then on the VERY LAST LINE output ONLY one of these two exact strings:\n"
                        f"VERDICT: PASS\n"
                        f"VERDICT: FAIL\n\n"
                        f"Do not add anything after the verdict line."
                    )
                    
                    qa_result = await asyncio.wait_for(
                        _proc(
                            f"{session_id}:swarm:qa_{agent_name}",
                            qa_prompt,
                            platform=platform,
                            routing_policy_override="QUALITY",  # QA always uses best model
                        ),
                        timeout=min(120.0, max(10.0, ctx.budget_remaining() - 5.0))
                    )
                    
                    lines = qa_result.strip().split("\n")
                    last_line = lines[-1].strip().upper() if lines else ""
                    if last_line == "VERDICT: PASS":
                        log.info("QA check passed for %s", agent_name)
                        break
                    
                    # QA FAILED — rollback files from this attempt and retry
                    ctx.generated_files = ctx.generated_files[:files_checkpoint]
                    log.info("QA failed for %s attempt %d/%d, retrying", agent_name, attempt+1, max_retries)
                    feedback_context += f"\n--- QA FEEDBACK (Attempt {attempt + 1}) ---\n{qa_result}\nPlease correct your previous output based on this feedback."

                except asyncio.TimeoutError:
                    log.warning("Swarm agent %s timed out | session=%s", agent_name, session_id)
                    ctx.scratchpad[agent_name] = f"[{agent_name}: timed out]"
                    ctx.steps_taken += 1
                    break
                except Exception as exc:
                    log.warning("Swarm agent %s failed | session=%s | err=%s", agent_name, session_id, exc)
                    ctx.scratchpad[agent_name] = f"[{agent_name}: failed — {exc}]"
                    ctx.steps_taken += 1
                    break

        # Restore original upload context so we can deliver final files
        if original_upload_ctx:
            set_upload_context(original_upload_ctx.platform, original_upload_ctx.target_id, is_internal_swarm=False)

        from tools.upload_tool import _deliver_file
        import os
        for final_file in ctx.generated_files:
            if os.path.exists(final_file):
                filename = os.path.basename(final_file)
                await _deliver_file(final_file, filename, description=f"📎 Final QA-Approved Document: {filename}")

        return await self._synthesize(ctx)

    async def _run_agent(
        self,
        agent_name: str,
        instructions: str,
        ctx: SwarmContext,
        routing_policy: str = "AUTO",
    ) -> str:
        """Invoke a dynamic specialist agent with its specific instructions + prior context."""
        from core.agent import process_message as _proc

        # Build prior work section for context handoff
        prior_sections = []
        for prev_agent, prev_output in ctx.scratchpad.items():
            prior_sections.append(f"=== {prev_agent} findings ===\n{prev_output}")
        prior_work = "\n\n".join(prior_sections)

        full_prompt = (
            f"You are a highly specialized AI agent. Your role is: {agent_name}\n"
            f"Your specific instructions for this task are:\n{instructions}\n\n"
            f"USER REQUEST: {ctx.original_query}\n"
            + (f"\n--- PRIOR AGENT WORK (use as context) ---\n{prior_work}\n---" if prior_work else "")
        )

        # Each sub-agent gets an isolated session ID to prevent context pollution
        agent_session = f"{ctx.session_id}:swarm:{agent_name.lower().replace('agent', '')}"
        return await _proc(
            agent_session,
            full_prompt,
            platform=ctx.platform,
            routing_policy_override=routing_policy,
        )

    async def _synthesize(self, ctx: SwarmContext) -> str:
        """Combine all agent outputs into one coherent final response."""
        from core.agent import process_message as _proc

        if not ctx.scratchpad:
            return "I was unable to complete this task. Please try rephrasing your request."

        # If only one agent ran and succeeded, return directly
        if len(ctx.scratchpad) == 1:
            only_output = list(ctx.scratchpad.values())[0]
            if not only_output.startswith('[') or 'failed' not in only_output:
                return only_output

        combined = "\n\n".join(
            f"=== {k} ===\n{v}" for k, v in ctx.scratchpad.items()
        )

        synthesis_prompt = (
            f"Multiple specialist AI agents worked on this user request. "
            f"Synthesize their outputs into ONE final, polished, well-structured response.\n\n"
            f"ORIGINAL USER REQUEST: {ctx.original_query}\n\n"
            f"AGENT OUTPUTS:\n{combined}\n\n"
            f"Instructions: Remove all redundancy. Resolve contradictions by choosing the most accurate info. "
            f"Present as a coherent, professional final answer with proper formatting."
        )

        synth_session = f"{ctx.session_id}:swarm:synthesis"
        try:
            return await asyncio.wait_for(
                _proc(synth_session, synthesis_prompt, platform=ctx.platform),
                timeout=min(settings.swarm_agent_timeout_seconds, max(5.0, ctx.budget_remaining())),
            )
        except asyncio.TimeoutError:
            log.warning("Swarm synthesis timed out | session=%s", ctx.session_id)
            # Return the last agent's output as the best fallback
            return list(ctx.scratchpad.values())[-1]
        except Exception as exc:
            log.warning("Swarm synthesis failed | session=%s | err=%s", ctx.session_id, exc)
            return list(ctx.scratchpad.values())[-1]


_supervisor: Optional[SwarmSupervisor] = None


def get_swarm() -> SwarmSupervisor:
    """Singleton accessor."""
    global _supervisor
    if _supervisor is None:
        _supervisor = SwarmSupervisor()
    return _supervisor


async def run_swarm(query: str, session_id: str, platform: str) -> str:
    """Public entry point for the swarm. Called by core/agent.py."""
    return await get_swarm().run(query, session_id, platform)
