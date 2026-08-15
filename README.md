# OmniAgent

A multi-platform AI assistant built for Discord and Telegram. It leverages Google Gemini 2.5 Pro via LangGraph to provide persistent conversation memory and tool use capabilities. 

## Features

- **Multi-Platform Support**: Seamlessly works on both Discord and Telegram.
- **Persistent Memory**: Uses a local SQLite database to maintain conversation history per user.
- **ReAct Agent**: Capable of step-by-step reasoning and tool execution.
- **Built-in Tools**: Includes tools for web search, Wikipedia lookup, math evaluation, time checking, sandboxed Python execution, weather, and fetching URL content.
- **Production Ready**: Includes structured logging, rate limiting, and docker support.

## Project Structure

- `main.py`: The main entry point. Initializes logging and starts bots.
- `config.py`: Configuration management using Pydantic.
- `core/`: Core logic including the LangGraph agent, rate limiting, and helpers.
- `tools/`: Implementations for the various tools the agent can use.
- `adapters/`: Platform-specific bots (Discord and Telegram).

## Setup

### Prerequisites

- Python 3.12 or higher.
- `uv` package manager (or `pip`).
- API keys for Google Gemini, and tokens for your Discord and/or Telegram bots.

### Installation

1. Clone the repository and navigate into the directory.
2. Copy the `.env.example` file to `.env` and fill in your API keys.

   ```bash
   cp .env.example .env
   ```

3. Install the dependencies using `uv`:

   ```bash
   uv sync
   ```

### Running Locally

To run the bot in your development environment:

```bash
uv run python main.py
```

### Docker Deployment

For a production or homelab environment, deploying via Docker is recommended.

```bash
docker-compose up -d --build
```

Logs can be viewed with:

```bash
docker-compose logs -f omniagent
```

Volumes are configured to persist your SQLite database and log files automatically.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.
