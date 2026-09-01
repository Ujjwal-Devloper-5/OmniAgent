<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:6C63FF,100:00C9FF&height=200&section=header&text=OmniAgent&fontSize=80&fontColor=ffffff&fontAlignY=38&desc=The%20Ultimate%20Self-Hosted%20AI%20Platform&descAlignY=60&descSize=20&animation=fadeIn" width="100%" />

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
  <a href="#"><img src="https://img.shields.io/badge/Slack-Bot-4A154B?style=for-the-badge&logo=slack&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Ollama-Local_AI-000000?style=for-the-badge&logo=ollama&logoColor=white" /></a>
  <a href="#"><img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=for-the-badge&logo=postgresql&logoColor=white" /></a>
</p>

<br/>

> **OmniAgent** is a production-grade, self-hosted AI platform for Discord, Telegram, and Slack.
> It uses a **scored model registry** to pick the smartest available model per task,
> lets every user have a **custom AI personality**, provides a **live admin dashboard**,
> runs code in an **isolated Docker sandbox**, and extends via **MCP servers** — all on your own hardware.

<br/>

[**Quick Start**](#-quick-start) · [**Architecture**](#%EF%B8%8F-architecture) · [**Features**](#-features) · [**Admin Dashboard**](#-admin-dashboard) · [**Model Registry**](#-dynamic-model-registry) · [**MCP Guide**](#-mcp-model-context-protocol) · [**Database**](#-database--enterprise) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🎯 Dynamic Model Registry
Every model is scored by `intelligence`, `speed`, and `tool_reliability`. OmniAgent automatically picks the highest-scoring model for each task — and if a model fails 3 times, its score drops and the next best takes over automatically.

</td>
<td width="50%">

### 🎭 Per-User Custom System Prompts
Every user can have their own AI personality. Set a custom system prompt for any Discord, Telegram, or Slack user from the Admin Dashboard. One bot — unlimited personas.

</td>
</tr>
<tr>
<td width="50%">

### 🖥️ Live Admin Dashboard
A beautiful dark-theme web dashboard at `http://your-server:8181`. Manage models, users, sessions, and watch live bot logs — all in real-time from your browser.

</td>
<td width="50%">

### ⚡ Instant Streaming Responses
Never waits silently. The bot instantly sends `🤔 Thinking...` and edits it live as the AI generates the answer — on Discord, Telegram, and Slack simultaneously.

</td>
</tr>
<tr>
<td width="50%">

### 🛡️ Self-Healing Memory
When MCP tools crash mid-execution, LangGraph leaves a corrupt checkpoint. OmniAgent **detects this automatically**, surgically wipes the bad state, and retries — the user never sees an error.

</td>
<td width="50%">

### 🧩 MCP — Model Context Protocol
Plug in any MCP server via `.env` — no code changes. Ships with Filesystem, Memory Graph, Sequential Thinking, and Puppeteer. Add GitHub, Jira, PostgreSQL, or any MCP server in 2 lines.

</td>
</tr>
<tr>
<td width="50%">

### 🔬 Isolated Execution Sandbox
Code runs in a dedicated **Ubuntu 24.04** Docker container — Python, pip, curl, git pre-installed, full internet access, persistent `/workspace`. Zero risk to the host.

</td>
<td width="50%">

### 🔄 Smart Context Trimming
After 40+ conversation turns, OmniAgent auto-summarizes old messages, wipes the bloated checkpoint, and continues — invisible to the user, zero context lost.

</td>
</tr>
<tr>
<td width="50%">

### 📡 OpenRouter Auto-Prober
Free OpenRouter models go offline constantly. A background daemon probes them every 12 hours and hot-swaps dead models from the pool — without a restart.

</td>
<td width="50%">

### 👁️ True Native Multimodal Vision
Images are downloaded as raw bytes and sent directly to vision model APIs. Telegram, Discord, and Slack all support real image downloads — the model actually sees your pixels.

</td>
</tr>
</table>

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Platform Layer                                 │
│   Discord  (streaming · vision · slash commands · member welcome)       │
│   Telegram (streaming · vision · voice · stickers · group context)      │
│   Slack    (socket mode · threads · vision · file uploads · DMs)        │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             Dynamic Model Registry + Smart Router                       │
│                                                                         │
│  Task Classifier (zero-latency keyword heuristics)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ CODING   │ │  MATH    │ │CREATIVE  │ │RESEARCH  │ │  VISION  │      │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘      │
│       └────────────┴────────────┴────────────┴────────────┘            │
│                                                                         │
│  Scoring Engine  (per task: intelligence×3 + speed×1 + tools×2)         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  gpt-4o           score: 54  vision:✅  tools:✅  → WINS         │   │
│  │  gemini-2.5-flash score: 51  vision:✅  tools:✅  → 2nd choice   │   │
│  │  qwen3:8b         score: 23  vision:❌  tools:❌  → SKIPPED      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│  Health: 3 failures → score demoted → next model takes over             │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   LangGraph ReAct Agent Engine                          │
│                                                                         │
│  ┌────────────────────┐  ┌──────────────────┐  ┌─────────────────────┐  │
│  │   Tool Registry    │  │   MCP Manager    │  │   Context Guard     │  │
│  │  (16+ built-in)    │  │                  │  │                     │  │
│  │ web_search         │  │ filesystem       │  │ Trim at 40+ msgs    │  │
│  │ execute_python     │  │ puppeteer        │  │ Auto-summarize      │  │
│  │ run_sandbox_cmd    │  │ memory graph     │  │ asyncio.Lock safe   │  │
│  │ fetch_url + more   │  │ sequential-think │  └─────────────────────┘  │
│  └────────────────────┘  └──────────────────┘                          │
│                                                                         │
│  ┌────────────────────┐  ┌──────────────────┐  ┌─────────────────────┐  │
│  │   Auto-Healer      │  │  Unified Memory  │  │ Per-User Prompts    │  │
│  │ Detect corrupt     │  │ PostgreSQL or    │  │ Custom AI persona   │  │
│  │ checkpoint → wipe  │  │ SQLite fallback  │  │ per user_id         │  │
│  │ → retry silently   │  │ Cross-provider   │  │ Admin Dashboard UI  │  │
│  └────────────────────┘  └──────────────────┘  └─────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
          ┌────────────────┴──────────────────┐
          ▼                                   ▼
┌──────────────────────┐          ┌────────────────────────┐
│  Execution Sandbox   │          │  Admin Dashboard        │
│  Ubuntu 24.04 Docker │          │  FastAPI on port 8181   │
│  pip · curl · git    │          │  Live logs · SSE stream │
│  Full internet       │          │  Model CRUD · User mgmt │
└──────────────────────┘          └────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Docker + Docker Compose** | Strongly recommended |
| **Python 3.12+** | For local development only |
| **One LLM API key** | Gemini, OpenRouter, Groq, OpenAI, or Anthropic |
| **Discord, Telegram, or Slack token** | At least one platform |

### 1. Clone & Configure

```bash
git clone https://github.com/your-repo/OmniAgent.git
cd OmniAgent
cp .env.example .env
```

Open `.env` and fill in at minimum:

```env
# Pick at least ONE AI provider
GEMINI_API_KEY=your_key_here        # https://aistudio.google.com (free)
OPENROUTER_API_KEY=your_key_here    # https://openrouter.ai       (free)
GROQ_API_KEY=your_key_here          # https://console.groq.com    (free)

# Pick at least ONE platform
DISCORD_TOKEN=your_discord_bot_token
TELEGRAM_BOT_TOKEN=your_telegram_token
# SLACK_BOT_TOKEN=xoxb-...          # Optional: see Slack section below
# SLACK_APP_TOKEN=xapp-...

# Optional: Enable the Admin Dashboard
# ADMIN_API_SECRET=your_secure_random_secret
```

### 2. Deploy

```bash
DOCKER_BUILDKIT=1 docker-compose up -d --build

# Watch live logs
docker-compose logs -f omniagent
```

### 3. Talk to It

- **Discord:** Mention `@YourBot` anywhere or DM it directly
- **Telegram:** Send any message to your bot
- **Slack:** `@mention` the bot in a channel or DM it

---

## 🖥️ Admin Dashboard

OmniAgent includes a complete web-based admin dashboard. To enable it, add one line to your `.env`:

```env
ADMIN_API_SECRET=any_long_random_secret_you_choose
```

Then restart and open **`http://your-server-ip:8181`** in your browser. Enter your secret to log in.

### Dashboard Panels

| Panel | What You Can Do |
|---|---|
| **📊 Overview** | Live provider health, active sessions count, model pool stats |
| **🤖 Models** | Add, edit scores, or delete any model from `models.json` — takes effect instantly without restart |
| **👥 Users** | View all users, set a **custom system prompt** for any user ID |
| **💬 Sessions** | See all active conversations across all platforms, clear any session |
| **📋 Live Logs** | Real-time color-coded log stream (INFO, WARNING, ERROR) via Server-Sent Events |
| **⚙️ Config** | Read-only view of all active settings |

### Per-User Custom System Prompts

In the Users panel, click any user and type their custom system prompt. For example:

- **User A (junior dev):** `"You are a patient coding tutor. Explain everything step by step with examples."`
- **User B (CEO):** `"You are a concise executive assistant. Bullet points only, no fluff."`
- **User C (customer support):** `"You work for Acme Corp. Only answer questions about our product."`

Every message that user sends will be shaped by their personal prompt — from any platform.

---

## 🎯 Dynamic Model Registry

OmniAgent scores every model mathematically and picks the best one per task. No guesswork.

### Scoring Formula

```
final_score = intelligence × 3.0
            + speed × 1.0
            + tool_reliability × 2.0  (when task requires tool-calling)
            + 5.0                      (vision bonus, when image attached)
            - 100.0                    (hard penalty for blind models on vision tasks)
            - (consecutive_failures × 20)  (health demotion on failures)
```

### Adding Models to the Registry

Add any model to `models.json`:

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

| Score | Meaning |
|---|---|
| **9-10** | Frontier models (GPT-4o, Claude 3.5, Gemini Pro) |
| **7-8** | Strong mid-tier (70B+, Gemini Flash) |
| **5-6** | Capable small models (32B, 14B) |
| **3-4** | 8B and smaller |
| **1-2** | Tiny models (1-3B) — don't use for tools |

Ollama models not listed are **auto-discovered** at boot with safe conservative defaults.

---

## 🔌 MCP (Model Context Protocol)

Add any MCP server via `.env` — no code changes.

### Pre-configured Servers

| Server | What It Gives the AI |
|---|---|
| `filesystem` | Read, write, search files in `/app` and `/app/data` |
| `memory` | Persistent knowledge graph across sessions |
| `sequential-thinking` | Multi-step chain-of-thought reasoning |
| `puppeteer` | Real Chromium browser — navigate, screenshot, fill forms |

### Adding a New MCP Server

```env
# Step 1: Add to the comma-separated list
MCP_SERVERS=filesystem,sequential_thinking,memory,puppeteer,github

# Step 2: Define it
MCP_GITHUB_COMMAND=npx
MCP_GITHUB_ARGS=-y,@modelcontextprotocol/server-github
```

Restart. The AI now has GitHub tools — zero code changes.

**More servers:**

```env
# Brave Search — real-time web results
MCP_BRAVE_COMMAND=npx
MCP_BRAVE_ARGS=-y,@modelcontextprotocol/server-brave-search

# PostgreSQL — query your DB in natural language
MCP_POSTGRES_COMMAND=npx
MCP_POSTGRES_ARGS=-y,@modelcontextprotocol/server-postgres,postgresql://user:pass@host/db

# Jira — create and manage tickets via AI
MCP_JIRA_COMMAND=npx
MCP_JIRA_ARGS=-y,@kazuph/mcp-jira
```

---

## 💬 Slack Setup

OmniAgent uses **Socket Mode** — no public IP, no reverse proxy needed.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Socket Mode** and generate an App-Level Token (`xapp-...`)
3. Add these **Bot Token Scopes:** `chat:write`, `app_mentions:read`, `im:read`, `im:write`, `files:read`, `users:read`
4. Enable **Event Subscriptions** and subscribe to: `app_mention`, `message.im`
5. Add to `.env`:

```env
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-level-token
SLACK_SIGNING_SECRET=your-signing-secret
```

Restart. The bot is now live in your Slack workspace.

**Slack-specific features:**
- `@mention` in any channel → bot replies in a **thread**
- DM the bot directly for private conversations
- Upload images → routed to vision AI (downloads privately via Bot Token)
- `/ask`, `/clear`, `/status`, `/help` slash commands

---

## 🗄️ Database & Enterprise

OmniAgent supports two database backends:

### SQLite (Default — Homelab & Solo Use)
Zero configuration. Data stored at `data/memory.sqlite`. Perfect for personal use and small teams. Handles hundreds of concurrent users without issues.

### PostgreSQL (Production — Teams & Enterprise)
For high-concurrency workloads (1000+ simultaneous conversations), switch to PostgreSQL. It's already included in `docker-compose.yml`.

**To enable PostgreSQL:**

```env
# Add to .env
DATABASE_URL=postgresql+asyncpg://omniagent:your_password@postgres:5432/omniagent
POSTGRES_PASSWORD=your_secure_password
```

> [!IMPORTANT]
> **Existing SQLite conversation history does not migrate automatically to PostgreSQL.** Only new conversations will be stored in PostgreSQL. If you want to preserve old history, contact us for a migration script.

> [!TIP]
> For most homelab and small business deployments, **SQLite is completely fine**. Only switch to PostgreSQL if you expect hundreds of simultaneous users.

---

## 🤖 Supported AI Providers

| Provider | Free Tier | Vision | Best For |
|---|---|---|---|
| **Google Gemini** | ✅ Yes | ✅ Yes | Research, math, coding, 1M token context |
| **OpenRouter** | ✅ Yes (200+ models) | ✅ Some | Largest variety of free and paid models |
| **Groq** | ✅ Yes | ❌ No | Speed — sub-second LPU inference |
| **Ollama** | ✅ Local | ✅ qwen2.5vl | Fully offline, zero data leaves your machine |
| **OpenAI** | 💳 Paid | ✅ Yes | GPT-4o — highest tool reliability |
| **Anthropic** | 💳 Paid | ✅ Yes | Claude — best for writing and analysis |

> You only need **one** to start. OmniAgent auto-detects which keys are configured and skips the rest.

---

## 🧰 Built-in Tool Ecosystem

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

Plus everything from your MCP servers — automatically discovered at boot.

---

## 📁 Project Structure

```
OmniAgent/
├── adapters/
│   ├── discord_bot.py          # Discord — streaming, vision, slash commands
│   ├── telegram_bot.py         # Telegram — streaming, vision, groups, voice
│   ├── slack_bot.py            # Slack — socket mode, threads, file uploads
│   ├── admin_api.py            # FastAPI admin API (port 8080 internal)
│   └── cogs/                   # Modular Discord slash command cogs
│
├── core/
│   ├── agents/                 # One agent class per AI provider
│   │   ├── base.py             # Shared: retry, auto-heal, async system prompt
│   │   ├── gemini_agent.py
│   │   ├── openrouter_agent.py
│   │   ├── ollama_agent.py
│   │   ├── groq_agent.py
│   │   ├── openai_agent.py
│   │   └── anthropic_agent.py
│   │
│   ├── model_router.py         # Smart routing engine + health quarantine
│   ├── model_registry.py       # Dynamic scoring engine — reads models.json
│   ├── memory.py               # UnifiedMemory — PostgreSQL + SQLite dual backend
│   ├── user_settings.py        # Per-user system prompt store
│   ├── context_manager.py      # Smart context trimming at 40+ turns
│   ├── stream_renderer.py      # Discord live animated responses
│   ├── user_brain.py           # Owner cognitive profile daemon
│   ├── rate_limiter.py         # Per-user RPM + daily token limits
│   └── health_monitor.py       # Background provider health checker
│
├── tools/
│   ├── registry.py             # Tool registration + system prompt injection
│   ├── mcp_manager.py          # MCP server lifecycle (env-driven)
│   ├── sandbox_tool.py         # Ubuntu 24.04 Docker sandbox orchestration
│   └── openrouter_prober.py    # Free model auto-discovery daemon
│
├── dashboard/
│   └── index.html              # Standalone admin dashboard (dark theme, no CDN)
│
├── models.json                 # Model registry — intelligence/speed/tool scores
├── Dockerfile                  # Multi-stage uv-powered build
├── docker-compose.yml          # PostgreSQL + OmniAgent production config
├── .env.example                # Fully documented configuration reference
└── CONTRIBUTING.md
```

---

## ⚙️ Configuration

```env
# ── AI Providers ──────────────────────────────────────────────────────────
GEMINI_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

# ── Platforms ─────────────────────────────────────────────────────────────
DISCORD_TOKEN=
TELEGRAM_BOT_TOKEN=
SLACK_BOT_TOKEN=            # Optional — xoxb-...
SLACK_APP_TOKEN=            # Optional — xapp-... (Socket Mode)

# ── Admin Dashboard ───────────────────────────────────────────────────────
ADMIN_API_SECRET=           # Set to enable dashboard at :8181

# ── Database ──────────────────────────────────────────────────────────────
# Leave blank for SQLite (default). Set for PostgreSQL (enterprise).
# DATABASE_URL=postgresql+asyncpg://omniagent:pass@postgres:5432/omniagent
# POSTGRES_PASSWORD=

# ── MCP Servers ───────────────────────────────────────────────────────────
MCP_SERVERS=filesystem,sequential_thinking,memory,puppeteer

# ── Performance Tuning ────────────────────────────────────────────────────
MODEL_FAILURE_THRESHOLD=3       # Failures before quarantine
MODEL_RECOVERY_SECONDS=300      # Seconds in quarantine before retry
RATE_LIMIT_RPM=20               # Max requests per minute per user
```

---

## 🤝 Contributing

- 🐛 **Bug Reports** — Open an issue with logs and reproduction steps
- 💡 **Feature Ideas** — Open a discussion before implementing
- 🔧 **Pull Requests** — Read [CONTRIBUTING.md](CONTRIBUTING.md) first
- 🤖 **Model Scores** — Add new models to `models.json` with accurate scores
- 🧩 **MCP Servers** — Document useful MCP server configs in the wiki

**Good first issues:** Add a model to `models.json` · add a new MCP preset · improve task classifier keywords · write tests · add a new slash command cog.

---

## 📄 License

Released under the **MIT License** — use it, modify it, sell it, ship it.

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:00C9FF,100:6C63FF&height=120&section=footer" width="100%" />

<br/>

**Built with ❤️ for the open-source community**

*If OmniAgent saved you time, drop a ⭐ — it helps others find it.*

</div>
