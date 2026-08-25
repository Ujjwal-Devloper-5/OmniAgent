# Contributing to OmniAgent

First off, thank you for considering contributing to OmniAgent! It's people like you that make OmniAgent such a powerful and flexible multi-agent framework.

## 🚀 How to Contribute

### 1. Reporting Bugs
If you find a bug, please open an issue in the repository. Provide as much detail as possible, including:
* Your OS and environment.
* The model provider(s) you are using.
* Steps to reproduce the bug.
* Expected vs. actual behavior.
* Logs (if applicable).

### 2. Suggesting Enhancements
Have an idea for a new feature? We'd love to hear it! Open an issue and describe:
* The feature you want.
* Why it would be useful.
* Possible implementation details or references to similar tools.

### 3. Submitting Pull Requests
1. **Fork the repository** and create your branch from `master`.
2. **Setup your environment:**
   We heavily recommend using `uv` for dependency management.
   ```bash
   uv sync
   ```
3. **Write your code:**
   Ensure your code follows the existing style conventions. We use standard Python typing (`typing` module) heavily for LangGraph state management.
4. **Test your code:**
   Run the bot locally and verify your changes work as expected.
   ```bash
   uv run python main.py
   ```
5. **Issue that pull request!**
   Describe your changes in detail in the PR description. If your PR resolves an open issue, link to it (e.g., `Fixes #123`).

## 🏗️ Development Guidelines

* **Multi-Provider Support:** When adding core features (like vision or streaming), ensure it cascades gracefully. If a provider doesn't support a feature, the `model_router.py` should intelligently skip it or fallback.
* **Tool Development:** New tools should be added to the `tools/` directory and registered in `tools/registry.py`. Ensure your docstrings are highly descriptive, as LangGraph uses these directly for the LLM prompt.
* **MCP Integration:** If you are building a tool that relies on complex external APIs (like GitHub or Google Drive), consider recommending it as an MCP server rather than hardcoding it into the core project.

## 📜 Code of Conduct
By participating in this project, you agree to abide by standard open-source community guidelines. Be respectful, constructive, and welcoming to new contributors.

Thank you for helping make OmniAgent better!
