<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:6C63FF,100:00C9FF&height=200&section=header&text=OmniAgent&fontSize=80&fontColor=ffffff&fontAlignY=38&desc=The%20Ultimate%20Self-Hosted%20AI%20Assistant&descAlignY=60&descSize=20&animation=fadeIn" width="100%" />

<br/>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/LangGraph-Powered-FF6B6B?style=for-the-badge&logo=chainlink&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/MCP-Native-00D084?style=for-the-badge&logo=anthropic&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/License-MIT-F7DF1E?style=for-the-badge&logo=opensourceinitiative&logoColor=black" /></a>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Discord-Bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Ollama-Local_AI-000000?style=for-the-badge&logo=ollama&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/OpenRouter-200%2B_Models-FF4500?style=for-the-badge&logo=openai&logoColor=white" /></a>
</p>

<br/>

> **OmniAgent** is a production-grade, self-hosted AI assistant for Discord & Telegram.
> It uses a **scored model registry** to pick the smartest available model per task,
> heals its own memory on crash, streams live responses, runs code in an **isolated Docker sandbox**,
> and extends infinitely via **MCP servers** — all on your own hardware.

<br/>

[**Quick Start**](#-quick-start) · [**Architecture**](#%EF%B8%8F-architecture) · [**Features**](#-features) · [**Model Registry**](#-dynamic-model-registry) · [**MCP Guide**](#-mcp-model-context-protocol) · [**Configuration**](#-configuration) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🎯 Dynamic Model Registry
Every model in your pool is scored by `intelligence`, `speed`, and `tool_reliability`. OmniAgent automatically picks the highest-scoring capable model for each task — not just the first available provider.

- Ask a coding question → highest-IQ model with tool reliability wins
- Say "hi" → fastest model wins
- Send an image → only vision-capable models compete
- A model fails 3 times → score drops, next best takes over

</td>
<td width="50%">

### ⚡ Instant Streaming Responses
Never waits silently. The moment you send a message, the bot replies with an animated status — `🤔 Thinking...` → `🧠 Processing... (3.2s)` — then **edits it in-place** with the real answer.

Zero dead silence. Zero perceived lag.

</td>
</tr>
<tr>
<td width="50%">

### 🛡️ Self-Healing Memory
When MCP tools crash mid-execution, LangGraph leaves a corrupt checkpoint that permanently breaks the session. OmniAgent **detects this automatically**, surgically wipes the bad SQLite rows, and **retries instantly** — no manual intervention, ever.

</td>
<td width="50%">

### 🧩 MCP — Model Context Protocol
Plug in any MCP server via `.env` — no code changes required. Ships pre-configured with:
- **Filesystem** — read/write/search your files
- **Memory** — persistent knowledge graph
- **Sequential Thinking** — multi-step reasoning
- **Puppeteer** — real headless browser automation

</td>
</tr>
<tr>
<td width="50%">

### 🔬 Isolated Execution Sandbox
Every code execution runs in a dedicated **Ubuntu 24.04** Docker container with Python, pip, curl, and git pre-installed. The container is created fresh, runs your code, and is destroyed — with zero risk to the host system.

- Full internet access + `pip install` anything
- Persistent `/workspace` per session
- 1GB RAM · 5-minute timeout · 256 PID limit

</td>
<td width="50%">

### 🔄 Smart Context Trimming
After 40+ conversation turns, OmniAgent automatically summarizes older messages into a compact block, wipes the bloated checkpoint, and re-injects a clean summary — **invisible to the user, zero context lost.**

Thread-safe with per-session `asyncio.Lock` to prevent race conditions.

</td>
</tr>
<tr>
<td width="50%">

### 📡 OpenRouter Auto-Prober
Free models on OpenRouter go down constantly. A background daemon probes every model every 12 hours, removes dead endpoints from the active pool, and hot-swaps the routing list **without a restart.**

</td>
<td width="50%">

### 👁️ True Native Multimodal Vision
Images sent in Discord or Telegram are downloaded as raw bytes, base64-encoded, and passed directly to vision model APIs. No URL passing, no scraping. **The model actually sees your image** at full pixel fidelity.

</td>
</tr>
</table>

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Platform Layer                               │
│   Discord  (streaming · image downloads · slash commands · DMs)     │
│   Telegram (text · photos · voice)                                  │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Dynamic Model Registry + Smart Router                  │
│                                                                     │
│  Task Classifier  (zero-latency keyword heuristics)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ CODING   │ │  MATH    │ │CREATIVE  │ │RESEARCH  │ │  VISION  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │
│       └────────────┴────────────┴────────────┘            │        │
│                       │                                   │        │
│   Scoring Engine  (intelligence×3 + speed×1 + tools×2)   │        │
│   ┌────────────────────────────────────────────────────┐  │        │
│   │  gemini-2.5-flash   score: 51  vision:✅  tools:✅  │  │        │
│   │  gpt-4o             score: 54  vision:✅  tools:✅  │  │        │
│   │  qwen3:8b           score: 23  vision:❌  tools:❌  │  │        │
│   │  → Winner: gpt-4o (highest score, passes filters)  │  │        │
│   └────────────────────────────────────────────────────┘  │        │
│   Health: 3 failures → score demoted → next model takes over       │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 LangGraph ReAct Agent Engine                        │
│                                                                     │
│  ┌──────────────────┐   ┌──────────────────┐  ┌─────────────────┐  │
│  │   Tool Registry  │   │  MCP Manager     │  │ Context Guard   │  │
│  │  (16+ tools)     │   │                  │  │                 │  │
│  │ web_search       │   │ filesystem       │  │ Trim at 40+msgs │  │
│  │ execute_python   │   │ puppeteer        │  │ Auto-summarize  │  │
│  │ run_sandbox_cmd  │   │ memory graph     │  │ Lock-protected  │  │
│  │ fetch_url · more │   │ sequential-think │  └─────────────────┘  │
│  └──────────────────┘   └──────────────────┘                       │
│                                                                     │
│  ┌──────────────────────┐              ┌──────────────────────┐     │
│  │     Auto-Healer      │              │    Unified Memory    │     │
│  │ Detect INVALID_CHAT_ │              │  SQLite WAL mode     │     │
│  │ HISTORY → wipe bad   │              │  Cross-provider log  │     │
│  │ checkpoint → retry   │              │  Session persists    │     │
│  └──────────────────────┘              └──────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Execution Sandbox                                │
│   Ubuntu 24.04 · python3 · pip · curl · git pre-installed         │
│   Per-session persistent /workspace · full internet access         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Docker + Docker Compose** | Strongly recommended for production |
| **Python 3.12+** | For local development only |
| **One LLM API key** | Gemini, OpenRouter, Groq, OpenAI, or Anthropic — all have free tiers |
| **Discord or Telegram token** | At least one platform required |

### 1. Clone & Configure

```bash
git clone https://github.com/your-repo/OmniAgent.git
cd OmniAgent
cp .env.example .env
```

Open `.env` and fill in at minimum:

```env
# Pick at least ONE — all have free tiers
GEMINI_API_KEY=your_key_here        # https://aistudio.google.com  (free)
OPENROUTER_API_KEY=your_key_here    # https://openrouter.ai        (free)
GROQ_API_KEY=your_key_here          # https://console.groq.com     (free)

# Pick at least ONE platform
DISCORD_TOKEN=your_discord_bot_token
TELEGRAM_BOT_TOKEN=your_telegram_token
```

### 2. Deploy

```bash
# Build and launch in production mode
DOCKER_BUILDKIT=1 docker-compose up -d --build

# Watch live logs
docker-compose logs -f omniagent
```

### 3. Talk to It

- **Discord:** Mention `@YourBot` anywhere, or DM it directly
- **Telegram:** Send any message to your bot

That's it. The bot is live.

---

## 🎯 Dynamic Model Registry

OmniAgent ships with a `models.json` file that defines every known model's capability scores. The router uses these scores to automatically pick the best model for each task — no hardcoded preferences, no guesswork.

### How Scoring Works

When you send a message, every available model is scored in real-time:

```
final_score = intelligence × 3.0
            + speed × 1.0
            + tool_reliability × 2.0   (when task requires tool-calling)
            + 5.0                       (vision bonus, when image attached)
            - 100.0                     (hard penalty for blind models on vision tasks)
            - (consecutive_failures × 20)  (health demotion)
```

**Example — "write a Python PDF generator" (CODING + tools needed):**

| Model | Intel | Speed | Tool Rel | Score | Result |
|---|---|---|---|---|---|
| `gpt-4o` | 9 | 7 | 10 | **54** | ✅ Winner |
| `gemini-2.5-flash` | 8 | 9 | 9 | **51** | 2nd choice |
| `llama-3.1-8b:free` | 4 | 9 | 3 | **24** | ❌ Skipped |
| `qwen3:8b` | 5 | 8 | 3 | **29** | ❌ Skipped |

The 8B model that previously hallucinated a fake screenshot **never even gets invited to handle the task.**

### Adding Models to the Registry

Open `models.json` and add your model:

```json
{
  "id": "your-model-id",
  "provider": "openrouter",
  "intelligence": 8,
  "speed": 7,
  "tool_reliability": 7,
  "vision": false,
  "context_window": 128000,
  "tags": ["coding", "general", "research"]
}
```

**Score guide:**

| Score | Intelligence | Speed | Tool Reliability |
|---|---|---|---|
| **9-10** | Frontier models (GPT-4o, Claude 3.5, Gemini Pro) | Sub-1s response | Reliably formats JSON tool calls |
| **7-8** | Strong mid-tier (70B+, Gemini Flash) | 1-3s | Usually correct, minor JSON slips |
| **5-6** | Capable small models (32B, 14B) | 3-6s | Sometimes hallucinates tool args |
| **3-4** | 8B and smaller | Fast | Often hallucinates or ignores tools |
| **1-2** | Tiny models (1B-3B) | Blazing | Do not use for tool tasks |

### Ollama Auto-Discovery

Any Ollama model installed on your machine but **not listed in `models.json`** is automatically discovered at boot and registered with safe conservative defaults (`intelligence: 4, speed: 7, tool_reliability: 2`). You can always add it to `models.json` to give it proper scores.

---

## 🔌 MCP (Model Context Protocol)

OmniAgent ships with 4 MCP servers pre-configured. Add more via `.env` — no code changes required.

### Pre-configured Servers

| Server | What It Gives the AI |
|---|---|
| `filesystem` | Read, write, search files in `/app` and `/app/data` |
| `memory` | Persistent knowledge graph — stores and recalls facts across sessions |
| `sequential-thinking` | Multi-step chain-of-thought reasoning for complex problems |
| `puppeteer` | Real Chromium browser — navigate pages, screenshot, fill forms, click |

### Adding a New MCP Server

Add entries to your `.env`:

```env
# Step 1: Add the server name to the comma-separated list
MCP_SERVERS=filesystem,sequential_thinking,memory,puppeteer,github

# Step 2: Define the new server command and args
MCP_GITHUB_COMMAND=npx
MCP_GITHUB_ARGS=-y,@modelcontextprotocol/server-github
```

Restart the container. The AI now has GitHub tools — automatically discovered, zero code changes.

**More servers to try:**

```env
# Brave Search — real-time web search with actual results
MCP_BRAVE_COMMAND=npx
MCP_BRAVE_ARGS=-y,@modelcontextprotocol/server-brave-search
MCP_BRAVE_ENV_BRAVE_API_KEY=your_brave_key

# PostgreSQL — query your database in natural language
MCP_POSTGRES_COMMAND=npx
MCP_POSTGRES_ARGS=-y,@modelcontextprotocol/server-postgres,postgresql://localhost/mydb

# Web Fetch — advanced page reader with markdown conversion
MCP_FETCH_COMMAND=uvx
MCP_FETCH_ARGS=mcp-server-fetch
```

---

## 🤖 Supported AI Providers

| Provider | Free Tier | Vision | Best For |
|---|---|---|---|
| **Google Gemini** | ✅ Yes | ✅ Yes | Research, math, coding, vision — 1M token context |
| **OpenRouter** | ✅ Yes (200+ models) | ✅ Some | Largest model variety, free & paid options |
| **Groq** | ✅ Yes | ❌ No | Speed — LPU inference, sub-second responses |
| **Ollama** | ✅ Local | ✅ qwen2.5vl | Fully offline, complete privacy, zero API cost |
| **OpenAI** | 💳 Paid | ✅ Yes | GPT-4o — highest tool reliability |
| **Anthropic** | 💳 Paid | ✅ Yes | Claude — best for creative writing and analysis |

> You only need **one** to get started. OmniAgent detects which keys are configured at boot and skips the rest automatically.

---

## 🧰 Built-in Tool Ecosystem

16+ tools available to the AI out of the box — plus all tools from your MCP servers:

```
🌐 Web & Research          🖥️  Sandbox Execution        💾 Memory & Files
─────────────────────      ──────────────────────────   ──────────────────────
web_search                 run_sandbox_command            remember_note
wikipedia_lookup           execute_python                 recall_notes
fetch_url                  write_sandbox_file             read_file
get_weather                read_sandbox_file              write_file
get_current_datetime       list_sandbox_files             list_files
calculate
```

---

## 📁 Project Structure

```
OmniAgent/
├── adapters/
│   ├── discord_bot.py          # Discord — streaming responses, vision, slash commands
│   ├── telegram_bot.py         # Telegram adapter
│   └── cogs/                   # Modular Discord slash command cogs
│       ├── ai_cog.py           # /ask, /model, /translate, /clear
│       ├── info_cog.py         # /status, /userinfo, /serverinfo, /help
│       ├── mod_cog.py          # /purge, /kick, /ban, /announce
│       ├── util_cog.py         # /poll, /remind, /avatar, /calculate
│       └── fun_cog.py          # /roll, /8ball, /coinflip
│
├── core/
│   ├── agents/                 # One agent class per AI provider
│   │   ├── base.py             # Shared: retry, auto-heal, system prompt builder
│   │   ├── gemini_agent.py     # Google Gemini (text + native vision PATH)
│   │   ├── openrouter_agent.py # OpenRouter (200+ models, free tier fallback)
│   │   ├── ollama_agent.py     # Local Ollama (fully offline, per-task model select)
│   │   ├── groq_agent.py       # Groq LPU (ultra-fast inference)
│   │   ├── openai_agent.py     # OpenAI GPT-4o / GPT-4o-mini
│   │   └── anthropic_agent.py  # Anthropic Claude
│   │
│   ├── model_router.py         # Smart routing engine + health quarantine
│   ├── model_registry.py       # Dynamic scoring engine — reads models.json
│   ├── context_manager.py      # Smart context trimming at 40+ checkpoint writes
│   ├── stream_renderer.py      # Discord live animated 🤔 → response
│   ├── memory.py               # Unified cross-model SQLite conversation memory
│   ├── user_brain.py           # Owner cognitive profiling daemon
│   ├── rate_limiter.py         # Per-user RPM + daily token limits
│   └── health_monitor.py       # Background provider health checker (5-min interval)
│
├── tools/
│   ├── registry.py             # Central tool registration + system prompt injection
│   ├── mcp_manager.py          # MCP server lifecycle (env-driven, stdio + SSE)
│   ├── sandbox_tool.py         # Ubuntu 24.04 Docker sandbox orchestration
│   ├── openrouter_prober.py    # Free model auto-discovery daemon (runs every 12h)
│   ├── search.py               # Web search tool
│   ├── file_tool.py            # Host filesystem read/write tools
│   └── ...                     # weather, datetime, calculator, wikipedia, url
│
├── models.json                 # Model registry — intelligence/speed/tool scores
├── Dockerfile                  # Multi-stage uv-powered build (Node.js + Chromium included)
├── docker-compose.yml          # Production config with SYS_ADMIN for Puppeteer
├── .env.example                # Fully documented configuration reference
└── CONTRIBUTING.md             # Contribution guide
```

---

## ⚙️ Configuration

The [`.env.example`](.env.example) file is a comprehensive, fully-commented reference for every option.

```env
# ── AI Providers ──────────────────────────────────────────────────────
GEMINI_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

# ── Platforms ─────────────────────────────────────────────────────────
DISCORD_TOKEN=
TELEGRAM_BOT_TOKEN=

# ── MCP Servers ───────────────────────────────────────────────────────
MCP_SERVERS=filesystem,sequential_thinking,memory,puppeteer
MCP_FILESYSTEM_COMMAND=npx
MCP_FILESYSTEM_ARGS=-y,@modelcontextprotocol/server-filesystem,/app,/app/data
# ... (see .env.example for full list)

# ── Performance Tuning ────────────────────────────────────────────────
MODEL_FAILURE_THRESHOLD=3       # Consecutive failures before quarantine
MODEL_RECOVERY_SECONDS=300      # Seconds in quarantine before auto-retry
RATE_LIMIT_RPM=20               # Max requests per minute per user
```

---

## 🤝 Contributing

Contributions are what make open-source thrive. All forms are welcome.

- 🐛 **Bug Reports** — Open an issue with logs and steps to reproduce
- 💡 **Feature Ideas** — Open a discussion before implementing
- 🔧 **Pull Requests** — Read [CONTRIBUTING.md](CONTRIBUTING.md) first
- 📖 **Documentation** — Fix typos, add examples, improve clarity
- 🎯 **Model Scores** — Add new models to `models.json` with accurate scores

**Good first issues:** Adding a new MCP server preset · adding a model to `models.json` · improving task classifier keywords · writing tests · adding a new slash command cog.

---

## 📄 License

Released under the **MIT License** — use it, modify it, ship it freely.

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:00C9FF,100:6C63FF&height=120&section=footer" width="100%" />

<br/>

**Built with ❤️ for the open-source community**

*If OmniAgent saved you time, drop a ⭐ — it helps others find it.*

</div>
