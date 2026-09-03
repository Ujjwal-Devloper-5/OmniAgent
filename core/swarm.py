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

log = get_logger(__name__)

MAX_STEPS = 5
SWARM_TIMEOUT = 90.0  # seconds total
AGENT_CHAR_CAP = 8000  # max chars per sub-agent output before synthesis


@dataclass
class SwarmContext:
    """Shared scratchpad passed between swarm agents."""
    original_query: str
    session_id: str
    platform: str
    scratchpad: dict[str, str] = field(default_factory=dict)
    steps_taken: int = 0
    start_time: float = field(default_factory=time.time)

    def budget_remaining(self) -> float:
        return SWARM_TIMEOUT - (time.time() - self.start_time)

    def is_over_budget(self) -> bool:
        return self.steps_taken >= MAX_STEPS or self.budget_remaining() < 5.0


# Specialist agent prompts — each agent is highly focused
_AGENT_PROMPTS: dict[str, str] = {
    'ResearchAgent': (
        "You are a deep research specialist. Your job is to gather comprehensive, "
        "factual information using web_search and wikipedia_lookup tools. "
        "Be thorough, cite sources when possible, and present findings clearly. "
        "DO NOT write a final report — just gather and present raw research findings."
    ),
    'CoderAgent': (
        "You are a senior software engineer. Write production-quality, well-commented code. "
        "Use execute_python or run_sandbox_command to test your code. "
        "Explain your implementation decisions concisely."
    ),
    'WriterAgent': (
        "You are a professional technical writer. Structure content with clear headers, "
        "bullet points, and logical flow. Create the final, polished output "
        "that will be delivered to the user. Be comprehensive yet concise."
    ),
    'AnalystAgent': (
        "You are a critical analyst. Analyze the provided information, identify key patterns, "
        "extract insights, evaluate pros/cons, and structure findings clearly. "
        "Focus on what matters most for the user's goal."
    ),
}

# Routing rules: task keywords → agent plan
_ROUTING_RULES: list[tuple[frozenset, list[str]]] = [
    (frozenset({'research', 'report', 'pdf', 'comprehensive', 'deep dive', 'investigate', 'study', 'overview'}),
     ['ResearchAgent', 'AnalystAgent', 'WriterAgent']),
    (frozenset({'code', 'implement', 'build', 'fix', 'debug', 'function', 'script', 'program'}),
     ['CoderAgent']),
    (frozenset({'analyze', 'analysis', 'compare', 'evaluate', 'assess', 'review', 'pros', 'cons'}),
     ['ResearchAgent', 'AnalystAgent', 'WriterAgent']),
    (frozenset({'write', 'draft', 'create', 'generate', 'compose', 'summarize'}),
     ['WriterAgent']),
]


def _plan_from_rules(query: str) -> list[str]:
    """Fast keyword-based routing. No LLM call needed — zero latency."""
    q = query.lower()
    for keywords, plan in _ROUTING_RULES:
        if any(kw in q for kw in keywords):
            return plan
    return ['ResearchAgent', 'WriterAgent']  # default


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

        plan = _plan_from_rules(query)
        log.info("Swarm started | session=%s | plan=%s", session_id, plan)

        for agent_name in plan:
            if ctx.is_over_budget():
                log.warning(
                    "Swarm budget exhausted | session=%s | steps=%d | elapsed=%.1fs",
                    session_id, ctx.steps_taken, time.time() - ctx.start_time
                )
                break

            try:
                result = await asyncio.wait_for(
                    self._run_agent(agent_name, ctx),
                    timeout=min(40.0, max(10.0, ctx.budget_remaining() - 5.0)),
                )
                ctx.scratchpad[agent_name] = result[:AGENT_CHAR_CAP]
                ctx.steps_taken += 1
                log.info(
                    "Swarm step %d/%d complete | agent=%s | output_len=%d",
                    ctx.steps_taken, MAX_STEPS, agent_name, len(result)
                )
            except asyncio.TimeoutError:
                log.warning("Swarm agent %s timed out | session=%s", agent_name, session_id)
                ctx.scratchpad[agent_name] = f"[{agent_name}: timed out]"
                ctx.steps_taken += 1
            except Exception as exc:
                log.warning("Swarm agent %s failed | session=%s | err=%s", agent_name, session_id, exc)
                ctx.scratchpad[agent_name] = f"[{agent_name}: failed — {exc}]"
                ctx.steps_taken += 1

        return await self._synthesize(ctx)

    async def _run_agent(self, agent_name: str, ctx: SwarmContext) -> str:
        """Invoke a specialist agent with focused prompt + prior context."""
        from core.agent import process_message as _proc

        specialist_prompt = _AGENT_PROMPTS.get(agent_name, _AGENT_PROMPTS['WriterAgent'])

        # Build prior work section for context handoff
        prior_sections = []
        for prev_agent, prev_output in ctx.scratchpad.items():
            prior_sections.append(f"=== {prev_agent} findings ===\n{prev_output}")
        prior_work = "\n\n".join(prior_sections)

        full_prompt = (
            f"{specialist_prompt}\n\n"
            f"USER REQUEST: {ctx.original_query}\n"
            + (f"\n--- PRIOR AGENT WORK (use as context) ---\n{prior_work}\n---" if prior_work else "")
        )

        # Each sub-agent gets an isolated session ID
        agent_session = f"{ctx.session_id}:swarm:{agent_name.lower().replace('agent', '')}"
        return await _proc(agent_session, full_prompt, platform=ctx.platform)

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
                timeout=min(30.0, max(5.0, ctx.budget_remaining())),
            )
        except asyncio.TimeoutError:
            log.warning("Swarm synthesis timed out | session=%s", ctx.session_id)
            # Return WriterAgent output as best fallback
            return ctx.scratchpad.get(
                'WriterAgent',
                list(ctx.scratchpad.values())[-1]
            )
        except Exception as exc:
            log.warning("Swarm synthesis failed | session=%s | err=%s", ctx.session_id, exc)
            return ctx.scratchpad.get('WriterAgent', list(ctx.scratchpad.values())[-1])


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
