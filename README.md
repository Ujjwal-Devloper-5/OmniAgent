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
  <a href="#"><img src="https://img.shields.io/badge/OpenRouter-Free_Tier-FF4500?style=for-the-badge&logo=openai&logoColor=white" /></a>
</p>

<br/>

> **OmniAgent** is a production-grade, self-hosted AI assistant for Discord & Telegram.
> It routes every request to the **best available model**, heals its own memory, streams live responses,
> runs code in an **isolated Docker sandbox**, and extends infinitely via **MCP servers** — all on your own hardware.

<br/>

[**Quick Start**](#-quick-start) · [**Architecture**](#%EF%B8%8F-architecture) · [**Features**](#-features) · [**MCP Guide**](#-mcp-model-context-protocol) · [**Configuration**](#-configuration) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🧠 Smart Multi-Provider Routing
Zero-latency keyword heuristics classify every message and route it to the optimal provider. Online providers always run before local GPU.

- **Coding** → DeepSeek / Qwen / GPT-4o
- **Vision** → Gemini Flash / Claude / local qwen2.5vl
- **Quick chat** → Groq LPU (blazing fast)
- **Fallback** → Ollama (fully offline)

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
When MCP tools crash mid-execution, LangGraph leaves a corrupt checkpoint that breaks the session. OmniAgent **detects this automatically**, wipes the bad state, and **retries instantly** — no intervention needed.

</td>
<td width="50%">

### 🧩 MCP — Model Context Protocol
Plug in any MCP server via `.env` — no code changes required. Ships pre-configured with:
- **Filesystem** — read/write/search your files
- **Memory** — persistent knowledge graph
- **Sequential Thinking** — multi-step reasoning
- **Puppeteer** — real browser automation

</td>
</tr>
<tr>
<td width="50%">

### 🔬 Isolated Execution Sandbox
Every code execution runs in a dedicated ephemeral **Ubuntu 24.04** container with:
- Full internet access + `pip install` anything
- Persistent workspace per session
- 1GB RAM · 5-minute timeout · 256 PID limit
- Zero risk to host system

</td>
<td width="50%">

### 🔄 Smart Context Trimming
After 40+ conversation turns, OmniAgent automatically summarizes older messages into a compact memory block, wipes the bloated checkpoint, and continues — **invisible to the user, zero context lost.**

</td>
</tr>
<tr>
<td width="50%">

### 📡 OpenRouter Auto-Prober
Free models on OpenRouter go down constantly. A background daemon probes every model every 12 hours, removes dead ones, and hot-swaps the routing list **without a restart.**

</td>
<td width="50%">

### 👁️ True Native Multimodal Vision
Images are downloaded as raw bytes, base64-encoded, and sent directly to vision model APIs. No URL scraping. **The model actually sees your image** at full fidelity.

</td>
</tr>
</table>

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Platform Layer                               │
│   Discord  (streaming responses · image downloads · slash commands) │
│   Telegram (text · photos · voice)                                  │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Smart Model Router                               │
│                                                                     │
│  Task Classifier  (zero-latency keyword heuristics)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ CODING   │ │  MATH    │ │CREATIVE  │ │RESEARCH  │ │  VISION  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │
│       └────────────┴────────────┴────────────┴────────────┘        │
│                                                                     │
│           Provider Priority Chain  (per task type)                  │
│    ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│    │  OpenAI  │→ │Anthropic │→ │  Gemini  │→ │  Groq    │→ ...     │
│    └──────────┘  └──────────┘  └──────────┘  └──────────┘          │
│    Health Monitor: 3 failures → quarantine → auto-recover           │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 LangGraph ReAct Agent Engine                        │
│                                                                     │
│  ┌──────────────────┐   ┌──────────────────┐  ┌─────────────────┐  │
│  │   Tool Registry  │   │  MCP Manager     │  │ Context Guard   │  │
│  │                  │   │                  │  │                 │  │
│  │ web_search       │   │ filesystem       │  │ Trim at 40+msgs │  │
│  │ calculate        │   │ puppeteer        │  │ Auto-summarize  │  │
│  │ execute_python   │   │ memory graph     │  │ Inject context  │  │
│  │ run_sandbox_cmd  │   │ sequential-think │  └─────────────────┘  │
│  │ fetch_url        │   └──────────────────┘                       │
│  │ remember_note    │                                               │
│  │ + more...        │   ┌──────────────────┐  ┌─────────────────┐  │
│  └──────────────────┘   │   Auto-Healer    │  │ Unified Memory  │  │
│                         │                  │  │                 │  │
│                         │ Detect corrupt   │  │ SQLite WAL mode │  │
│                         │ checkpoint →     │  │ Cross-provider  │  │
│                         │ wipe → retry     │  │ history log     │  │
│                         └──────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Execution Sandbox                                │
│   Ubuntu 24.04 Docker container per session                        │
│   pip · curl · git · full internet — isolated persistent workspace │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Docker + Docker Compose** | Strongly recommended |
| **Python 3.12+** | For local development only |
| **One LLM API key** | Gemini, OpenRouter, Groq, OpenAI, Anthropic — all have free tiers |
| **Discord or Telegram Token** | At least one platform |

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

## 🔌 MCP (Model Context Protocol)

OmniAgent ships with 4 MCP servers pre-configured. Add more via `.env` — no code changes required.

### Pre-configured Servers

| Server | What It Gives the AI |
|---|---|
| `filesystem` | Read, write, search files in `/app` and `/app/data` |
| `memory` | Persistent knowledge graph — stores and recalls facts across sessions |
| `sequential-thinking` | Multi-step chain-of-thought reasoning for complex problems |
| `puppeteer` | Real Chromium browser — navigate pages, screenshot, fill forms |

### Adding a New MCP Server

Add 3 lines to your `.env`:

```env
# Step 1: Add the server name to the list
MCP_SERVERS=filesystem,sequential_thinking,memory,puppeteer,github

# Step 2: Define it
MCP_GITHUB_COMMAND=npx
MCP_GITHUB_ARGS=-y,@modelcontextprotocol/server-github
```

Restart. The AI now has GitHub tools — automatically discovered, no code touched.

**Popular servers to add:**

```env
# Brave Search — real-time web search
MCP_BRAVE_COMMAND=npx
MCP_BRAVE_ARGS=-y,@modelcontextprotocol/server-brave-search

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
| **Google Gemini** | ✅ Yes | ✅ Yes | Research, math, coding, vision |
| **OpenRouter** | ✅ Yes (200+ models) | ❌ Free tier | General tasks, coding, creative |
| **Groq** | ✅ Yes | ❌ No | Speed — fastest responses on the planet |
| **Ollama** | ✅ Local | ✅ qwen2.5vl | Fully offline, complete privacy |
| **OpenAI** | 💳 Paid | ✅ Yes | GPT-4o — premium quality |
| **Anthropic** | 💳 Paid | ✅ Yes | Claude — creative writing, analysis |

> You only need **one** to get started. The router automatically works with whatever keys you provide and skips the rest.

---

## 🧰 Built-in Tool Ecosystem

16+ tools available to the AI out of the box — plus everything from your MCP servers:

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
│   ├── discord_bot.py          # Discord — streaming, vision, slash commands
│   ├── telegram_bot.py         # Telegram adapter
│   └── cogs/                   # Modular Discord slash command cogs
│
├── core/
│   ├── agents/                 # One file per AI provider backend
│   │   ├── base.py             # Shared: retry logic, auto-heal, system prompt
│   │   ├── gemini_agent.py     # Google Gemini (text + native vision)
│   │   ├── openrouter_agent.py # OpenRouter (200+ free models with fallback)
│   │   ├── ollama_agent.py     # Local Ollama (fully offline)
│   │   ├── groq_agent.py       # Groq (LPU ultra-fast inference)
│   │   ├── openai_agent.py     # OpenAI GPT-4o
│   │   └── anthropic_agent.py  # Anthropic Claude
│   │
│   ├── model_router.py         # Smart routing engine + health tracking
│   ├── context_manager.py      # Smart context trimming at 40+ turns
│   ├── stream_renderer.py      # Discord live animated response
│   ├── memory.py               # Unified cross-model SQLite memory
│   ├── user_brain.py           # Owner cognitive profiling daemon
│   ├── rate_limiter.py         # Per-user rate limiting
│   └── health_monitor.py       # Background provider health checker
│
├── tools/
│   ├── registry.py             # Central tool registration hub
│   ├── mcp_manager.py          # MCP server lifecycle manager
│   ├── sandbox_tool.py         # Docker sandbox orchestration
│   ├── openrouter_prober.py    # Free model auto-discovery daemon
│   ├── search.py               # Web search tool
│   ├── file_tool.py            # File read/write tools
│   └── ...
│
├── Dockerfile                  # Multi-stage uv-powered optimized build
├── docker-compose.yml          # Production deployment config
├── .env.example                # Fully documented configuration reference
└── CONTRIBUTING.md             # Contribution guide
```

---

## ⚙️ Configuration

The [`.env.example`](.env.example) file is a comprehensive, fully-commented reference for every option. Key sections:

```env
# ── AI Providers ─────────────────────────────────────────────────────
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

**Good first issues:** Adding a new MCP server preset · improving task classifier keywords · adding a new slash command · writing tests.

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
