"""
OmniAgent Professional Task Classifier
═══════════════════════════════════════════════════════════════════
Runs on every single prompt. Pure Python. Zero latency. No LLM call.

Produces a TaskDecision that drives:
  - Which model tier to use (FAST / BALANCED / POWERFUL)
  - Whether to activate the Agent Swarm
  - Which specific capabilities to request from the router
  - Whether the user wants file output
  - Confidence score for the classification

Signals used (multi-dimensional, not just keywords):
  1. Token/word count → complexity proxy
  2. Sentence structure → simple question vs complex request
  3. Technical term density → coding/math/research classification
  4. Output intent signals → file, report, pdf, chart, table
  5. Urgency signals → quick, fast, brief → FAST tier
  6. Depth signals → comprehensive, detailed, thorough → POWERFUL + swarm
  7. Question type → how/what/why/explain → different routing
  8. Code markers → backticks, function names, error traces
  9. Media signals → image/photo/screenshot → VISION
  10. Multi-step signals → "and then", "also", "after that"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)


class TaskType(str, Enum):
    QUICK    = "quick"      # short factual, time/date, simple Q&A
    CODING   = "coding"     # code generation, debugging, refactoring
    MATH     = "math"       # calculations, equations, statistics
    CREATIVE = "creative"   # writing, poetry, stories, brainstorming
    RESEARCH = "research"   # multi-source info gathering
    ANALYSIS = "analysis"   # comparison, evaluation, critique
    VISION   = "vision"     # image/file understanding
    GENERAL  = "general"    # catch-all


class ComplexityLevel(str, Enum):
    LOW    = "low"     # one-liner answer, < 30 words input
    MEDIUM = "medium"  # paragraph answer, 30-150 words input
    HIGH   = "high"    # multi-section, > 150 words OR highly technical


class ModelTier(str, Enum):
    FAST      = "fast"      # groq, ollama fast models — <1s response
    BALANCED  = "balanced"  # openrouter free, gemini flash — good quality
    POWERFUL  = "powerful"  # gpt-4o, claude, gemini pro — best quality


@dataclass
class TaskDecision:
    """Complete routing decision for a single user prompt."""
    task_type:             TaskType
    complexity:            ComplexityLevel
    model_tier:            ModelTier
    use_swarm:             bool
    requires_file_output:  bool
    capabilities_needed:   list[str]      # fed directly to model router
    confidence:            float          # 0.0–1.0
    rationale:             str            # logged for debugging

    # Convenience
    @property
    def is_quick(self) -> bool:
        return self.model_tier == ModelTier.FAST

    @property
    def is_complex(self) -> bool:
        return self.complexity == ComplexityLevel.HIGH


# Pre-compiled word boundary pattern cache
_WB_PATTERN_CACHE: dict[str, re.Pattern] = {}

def _word_in_text(word: str, text: str) -> bool:
    """Check if `word` appears as a whole word in `text` (word-boundary aware)."""
    if word not in _WB_PATTERN_CACHE:
        _WB_PATTERN_CACHE[word] = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
    return bool(_WB_PATTERN_CACHE[word].search(text))

# ─── Signal word sets ──────────────────────────────────────────────────────────

_QUICK_SIGNALS = frozenset({
    "what time", "what date", "today", "current time", "hi", "hello",
    "hey", "thanks", "thank you", "ok", "okay", "sure", "yes",
    "how are you", "what's up", "who are you", "your name",
    "translate", "define", "meaning of", "spell", "synonym",
    "weather", "temperature",
})

_CODING_SIGNALS = frozenset({
    "code", "function", "class", "method", "debug", "error", "bug", "fix",
    "implement", "algorithm", "script", "program", "refactor", "optimize",
    "python", "javascript", "typescript", "java", "rust", "golang", "c++",
    "sql", "api", "endpoint", "regex", "compile", "syntax", "library",
    "import", "exception", "stack trace", "dockerfile", "yaml", "json",
    "html", "css", "bash", "shell", "git", "deploy", "docker", "async",
    "database", "orm", "flask", "fastapi", "django", "react", "vue",
    "write a function", "write code", "write a script", "build a",
    "create a function", "make a class", "parse", "serialize",
})

_MATH_SIGNALS = frozenset({
    "calculate", "compute", "solve", "equation", "formula", "integral",
    "derivative", "matrix", "statistics", "probability", "percentage",
    "convert", "how many", "how much", "sum of", "average", "mean",
    "median", "variance", "standard deviation", "factorial", "prime",
    "algebra", "geometry", "trigonometry", "calculus", "proof",
})

_CREATIVE_SIGNALS = frozenset({
    "write a story", "write a poem", "write an essay", "creative",
    "brainstorm", "ideas for", "imagine", "fiction", "narrative",
    "character", "plot", "dialogue", "metaphor", "slogan", "tagline",
    "ad copy", "marketing", "pitch", "persuasive", "song", "lyrics",
    "joke", "funny", "humor", "satire", "parody",
})

_RESEARCH_SIGNALS = frozenset({
    "research", "find out", "what is", "who is", "when did", "where is",
    "history of", "explain", "tell me about", "information about",
    "facts about", "overview of", "background on", "learn about",
    "look up", "search for", "find information", "gather", "investigate",
})

_ANALYSIS_SIGNALS = frozenset({
    "analyze", "analysis", "compare", "contrast", "evaluate", "assess",
    "review", "pros and cons", "advantages", "disadvantages", "critique",
    "strengths", "weaknesses", "swot", "difference between", "versus",
    "vs", "better", "worse", "recommend", "which is", "should i",
    "tradeoffs", "benchmark",
})

_DEPTH_SIGNALS = frozenset({
    "comprehensive", "detailed", "in-depth", "thorough", "exhaustive",
    "complete", "full", "extensive", "deep dive", "deep research",
    "step by step", "in detail", "elaborate", "expand on",
    "everything about", "all aspects",
})

_FILE_OUTPUT_SIGNALS = frozenset([
    "pdf", "excel", "spreadsheet", "powerpoint", "docx", "download",
    "export", "generate report", "create report", "make a report",
    "write a report", "create a file", "save to file", "save as pdf",
    "create a pdf", "make a pdf", "generate pdf", "save as excel",
    "csv file", "zip file", "archive", "attachment",
])

_VISION_SIGNALS = frozenset({
    "image", "photo", "picture", "screenshot", "diagram", "chart",
    "graph", "figure", "look at", "see this", "what do you see",
    "describe this", "read this image", "ocr", "extract text from",
})

_MULTISTEP_SIGNALS = frozenset({
    "and then", "after that", "also", "additionally", "furthermore",
    "first", "second", "third", "finally", "step 1", "step 2",
    "multiple", "several", "various", "list of", "comprehensive list",
})

_URGENCY_SIGNALS = frozenset({
    "quick", "quickly", "fast", "brief", "short", "tldr", "tl;dr",
    "summary", "summarize", "briefly", "in a nutshell", "one line",
    "simple",
})


def _word_count(text: str) -> int:
    return len(text.split())


def _has_code_markers(text: str) -> bool:
    """True if message contains code fences, indented blocks, or traceback patterns."""
    return bool(
        re.search(r'```', text)
        or re.search(r'\bTraceback\b|\bException\b|\bSyntaxError\b', text)
        or re.search(r'def \w+\(|class \w+[:(]|import \w+', text)
        or re.search(r'\$\s+\w+|>>>\s', text)  # shell prompt or Python REPL
    )


def _signal_score(text_lower: str, signal_set: frozenset) -> int:
    """Count how many signals from a set are present in the text."""
    score = 0
    for signal in signal_set:
        # Use word-boundary for single short words that could false-positive
        if len(signal) <= 4 and ' ' not in signal:
            if _word_in_text(signal, text_lower):
                score += 1
        else:
            if signal in text_lower:
                score += 1
    return score


def classify(message: str, has_media: bool = False, platform: str = "") -> TaskDecision:
    """
    Classify a user prompt into a full TaskDecision.
    Pure Python, sub-millisecond. Called on every single message.
    """
    text = message.strip()
    text_lower = text.lower()
    words = _word_count(text)

    # ─── Complexity ────────────────────────────────────────────────────────
    if words < 15 and not _has_code_markers(text):
        complexity = ComplexityLevel.LOW
    elif words < 80 and _signal_score(text_lower, _DEPTH_SIGNALS) == 0:
        complexity = ComplexityLevel.MEDIUM
    else:
        complexity = ComplexityLevel.HIGH

    # Depth signals always push to HIGH
    if _signal_score(text_lower, _DEPTH_SIGNALS) >= 1:
        complexity = ComplexityLevel.HIGH

    # Multi-step pushes to at least MEDIUM
    if _signal_score(text_lower, _MULTISTEP_SIGNALS) >= 2 and complexity == ComplexityLevel.LOW:
        complexity = ComplexityLevel.MEDIUM

    # ─── Task Type ─────────────────────────────────────────────────────────
    if has_media:
        task_type = TaskType.VISION
        capabilities = ["vision"]
    elif _has_code_markers(text) or _signal_score(text_lower, _CODING_SIGNALS) >= 2:
        task_type = TaskType.CODING
        capabilities = ["coding", "text"]
    elif _signal_score(text_lower, _MATH_SIGNALS) >= 2:
        task_type = TaskType.MATH
        capabilities = ["math", "text"]
    elif _signal_score(text_lower, _ANALYSIS_SIGNALS) >= 2:
        task_type = TaskType.ANALYSIS
        capabilities = ["analysis", "research", "text"]
    elif _signal_score(text_lower, _CREATIVE_SIGNALS) >= 1:
        task_type = TaskType.CREATIVE
        capabilities = ["creative", "text"]
    elif _signal_score(text_lower, _RESEARCH_SIGNALS) >= 2:
        task_type = TaskType.RESEARCH
        capabilities = ["research", "text"]
    elif _signal_score(text_lower, _QUICK_SIGNALS) >= 1 and words < 20:
        task_type = TaskType.QUICK
        capabilities = ["quick", "text"]
    else:
        task_type = TaskType.GENERAL
        capabilities = ["general", "text"]

    # ─── File output intent ────────────────────────────────────────────────
    requires_file_output = (
        _signal_score(text_lower, _FILE_OUTPUT_SIGNALS) >= 1
        or ("report" in text_lower and any(w in text_lower for w in ["create", "generate", "make", "write", "build"]))
        or ("document" in text_lower and any(w in text_lower for w in ["create", "generate", "make", "write"]))
    )

    # ─── Model Tier ────────────────────────────────────────────────────────
    urgency = _signal_score(text_lower, _URGENCY_SIGNALS) >= 1

    if urgency and complexity != ComplexityLevel.HIGH:
        model_tier = ModelTier.FAST
    elif complexity == ComplexityLevel.LOW and task_type == TaskType.QUICK:
        model_tier = ModelTier.FAST
    elif complexity == ComplexityLevel.HIGH or task_type in (
        TaskType.CODING, TaskType.MATH, TaskType.VISION, TaskType.ANALYSIS
    ):
        model_tier = ModelTier.POWERFUL
    else:
        model_tier = ModelTier.BALANCED

    # File output always warrants at least BALANCED
    if requires_file_output and model_tier == ModelTier.FAST:
        model_tier = ModelTier.BALANCED

    # ─── Swarm Decision ────────────────────────────────────────────────────
    # Swarm is warranted when task is genuinely multi-step AND complex
    depth_score = _signal_score(text_lower, _DEPTH_SIGNALS)
    research_score = _signal_score(text_lower, _RESEARCH_SIGNALS)
    multistep_score = _signal_score(text_lower, _MULTISTEP_SIGNALS)
    analysis_score = _signal_score(text_lower, _ANALYSIS_SIGNALS)

    use_swarm = (
        # High-complexity research/analysis tasks
        (complexity == ComplexityLevel.HIGH and task_type in (TaskType.RESEARCH, TaskType.ANALYSIS))
        # Deep research explicitly requested
        or (depth_score >= 1 and research_score >= 1)
        # File output with research or analysis
        or (requires_file_output and task_type in (TaskType.RESEARCH, TaskType.ANALYSIS, TaskType.CREATIVE) and complexity != ComplexityLevel.LOW)
        # Explicitly multi-step + complex
        or (multistep_score >= 3 and complexity == ComplexityLevel.HIGH)
        # Research + analysis combo (e.g. "research X and compare with Y")
        or (research_score >= 1 and analysis_score >= 1 and complexity != ComplexityLevel.LOW)
    )

    # Never swarm for quick/simple/code/math — they don't benefit from it
    if task_type in (TaskType.QUICK, TaskType.CODING, TaskType.MATH, TaskType.VISION):
        use_swarm = False
        
    # EXPLICIT OVERRIDE: Always swarm if the user asks for a file (PDFs/scripts) or explicitly requests swarm
    if "swarm" in text_lower or "multi-agent" in text_lower or requires_file_output:
        use_swarm = True

    # ─── Confidence ────────────────────────────────────────────────────────
    # Higher confidence when multiple strong signals align
    total_signals = (
        _signal_score(text_lower, _CODING_SIGNALS)
        + _signal_score(text_lower, _MATH_SIGNALS)
        + _signal_score(text_lower, _RESEARCH_SIGNALS)
        + _signal_score(text_lower, _CREATIVE_SIGNALS)
        + _signal_score(text_lower, _ANALYSIS_SIGNALS)
    )
    confidence = min(0.95, 0.5 + (total_signals * 0.08))
    if has_media:
        confidence = 0.99  # Vision is unambiguous
    if task_type == TaskType.QUICK and words < 10:
        confidence = 0.90

    # ─── Rationale (for logging) ───────────────────────────────────────────
    rationale = (
        f"type={task_type.value} complexity={complexity.value} tier={model_tier.value} "
        f"swarm={use_swarm} file={requires_file_output} words={words} conf={confidence:.2f}"
    )
    log.debug("TaskClassifier: %s", rationale)

    return TaskDecision(
        task_type=task_type,
        complexity=complexity,
        model_tier=model_tier,
        use_swarm=use_swarm,
        requires_file_output=requires_file_output,
        capabilities_needed=capabilities,
        confidence=confidence,
        rationale=rationale,
    )
