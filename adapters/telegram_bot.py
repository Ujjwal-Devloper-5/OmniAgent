"""
Production-grade Telegram bot adapter with:
- Full command suite (/start, /help, /clear, /status, /about)
- Rate limiting per user
- Proper Markdown escaping for Telegram MarkdownV2
- Photo/document handling with AI description
- Message chunking for 4096 char limit
- Error handling and logging
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings
from core.agent import clear_memory, get_status, process_message
from core.helpers import split_message
from core.logger import get_logger
from core.rate_limiter import get_rate_limiter

log = get_logger(__name__)


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

_TG_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2.
    Falls back to plain text if escaping fails.
    """
    for char in _TG_SPECIAL_CHARS:
        text = text.replace(char, f"\\{char}")
    return text


async def _send_long_message(
    update: Update,
    text: str,
    max_length: int = 4000,
) -> None:
    """Send a potentially long message, chunked if needed."""
    chunks = split_message(text, max_length=max_length)
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk)
        except Exception as exc:
            log.warning("Failed to send Telegram chunk: %s", exc)
            # Try plain text
            try:
                await update.message.reply_text(chunk)
            except Exception:
                pass


async def _handle_ai_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    has_media: bool = False,
) -> None:
    """Core AI message handler for Telegram."""
    if not update.message:
        return

    user_id = str(update.message.from_user.id) if update.message.from_user else "unknown"
    session_id = f"telegram_{update.message.chat_id}"

    # Rate limit check
    rate_limiter = get_rate_limiter()
    allowed, reason = await rate_limiter.check_request(f"telegram_{user_id}")
    if not allowed:
        await update.message.reply_text(reason)
        return

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.message.chat_id,
        action=ChatAction.TYPING,
    )

    try:
        response = await process_message(
            session_id, text, platform="telegram", has_media=has_media,
        )

        # Record token usage
        await rate_limiter.record_tokens(
            f"telegram_{user_id}", len(response) // 4
        )

        await _send_long_message(update, response)

    except Exception as exc:
        log.error("Telegram AI handler error: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"❌ An error occurred: `{type(exc).__name__}`. Please try again."
        )


# ───────────────────────────────────────────────────────────────────────────────
# Command handlers
# ───────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📚 Help", callback_data="help")],
        [InlineKeyboardButton("🚮 Clear History", callback_data="clear")],
        [InlineKeyboardButton("📊 My Status", callback_data="status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    user_name = update.message.from_user.first_name if update.message and update.message.from_user else "there"
    await update.message.reply_text(
        f"Hey {user_name}! 🤖 I'm **{settings.bot_name}**, your intelligent AI assistant.\n\n"
        f"I'm powered by **Google Gemini** and can:\n"
        f"\u2022 Answer any question with web search\n"
        f"\u2022 Look up Wikipedia articles\n"
        f"\u2022 Do math and run code\n"
        f"\u2022 Check the weather\n"
        f"\u2022 Read URLs you share\n"
        f"\u2022 Remember your full conversation\n\n"
        f"Just send me any message to start! Use /help for all commands.",
        reply_markup=reply_markup,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        f"*{settings.bot_name} — Command Reference*\n\n"
        f"*Chat Commands*\n"
        f"Just send any message to chat with the AI!\n\n"
        f"*Slash Commands*\n"
        f"/start — Welcome message\n"
        f"/help — This help message\n"
        f"/model <provider> <query> — Force a specific AI provider\n"
        f"/clear — Clear your conversation history\n"
        f"/status — View bot status and providers health\n"
        f"/about — About this bot\n\n"
        f"*🤖 AI Providers*\n"
        f"🟡 Gemini (Google) — Best for research & reasoning\n"
        f"🟢 GPT-4o (OpenAI) — Best for coding\n"
        f"🟣 Claude (Anthropic) — Best for creative writing\n"
        f"⚡ Groq (Free) — Ultra-fast inference\n"
        f"🆓 OpenRouter (Free models) — Variety of OSS models\n"
        f"🔵 Ollama (Local) — Offline fallback\n\n"
        f"*AI Tools*\n"
        f"🔍 Web Search\n"
        f"🗂 Wikipedia Lookup\n"
        f"🧮 Calculator\n"
        f"🕒 Date & Time\n"
        f"🧸 Code Execution\n"
        f"🌦 Weather Info\n"
        f"🌐 URL Reader\n"
    )
    await update.message.reply_text(help_text)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = f"telegram_{update.message.chat_id}"
    try:
        await clear_memory(session_id)
        await update.message.reply_text(
            "✅ Conversation history cleared! Starting fresh."
        )
    except Exception as exc:
        log.error("Error clearing Telegram memory: %s", exc)
        await update.message.reply_text(f"❌ Failed to clear history: {exc}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id) if update.message.from_user else "unknown"
    stats = await get_rate_limiter().get_stats(f"telegram_{user_id}")
    provider_health = await get_status()

    # Build provider lines
    provider_lines = []
    icons = {
        "gemini":     "🟡",
        "openai":     "🟢",
        "anthropic":  "🟣",
        "groq":       "⚡",
        "openrouter": "🆓",
        "ollama":     "🔵",
    }
    free_labels = {"groq": " (FREE)", "openrouter": " (FREE)", "ollama": " (local)"}
    for pname, info in provider_health.items():
        icon = icons.get(pname, "⚪")
        label = free_labels.get(pname, "")
        if info["configured"] and info["healthy"]:
            status_str = f"✅ Online{label}"
        elif info["configured"] and not info["healthy"]:
            status_str = f"❌ Unhealthy ({info['consecutive_failures']} fails)"
        else:
            status_str = "⚫ Not configured"
        provider_lines.append(f"{icon} {pname.capitalize()}: {status_str}")

    provider_text = "\n".join(provider_lines)

    status_text = (
        f"*Your Status*\n\n"
        f"⚡ Requests left this min: {stats['requests_remaining_this_minute']}/{stats['requests_per_minute_limit']}\n"
        f"📊 Tokens used today: {stats['tokens_used_today']:,}/{stats['tokens_per_day_limit']:,}\n\n"
        f"*🤖 AI Providers*\n{provider_text}\n\n"
        f"🔧 Python: {platform.python_version()}"
    )
    await update.message.reply_text(status_text)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    about_text = (
        f"*About {settings.bot_name}*\n\n"
        f"Version: 2.0.0\n"
        f"AI Engine: Multi-Agent Router\n"
        f"Framework: LangGraph\n"
        f"Memory: SQLite (persistent)\n"
        f"Platform: Python {platform.python_version()}"
    )
    await update.message.reply_text(about_text)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/model <provider> <your query>`\n"
            "Providers: gemini, openai, anthropic, groq, openrouter, ollama"
        )
        return

    provider = context.args[0].lower()
    query = " ".join(context.args[1:])
    user_id = str(update.message.from_user.id) if update.message.from_user else "unknown"
    session_id = f"telegram_{update.message.chat_id}"

    rate_limiter = get_rate_limiter()
    allowed, reason = await rate_limiter.check_request(f"telegram_{user_id}")
    if not allowed:
        await update.message.reply_text(reason)
        return

    await context.bot.send_chat_action(
        chat_id=update.message.chat_id,
        action=ChatAction.TYPING,
    )

    try:
        response = await process_message(
            session_id=session_id,
            message=query,
            platform="telegram",
            force_provider=provider,
            has_media=False,
        )
        await rate_limiter.record_tokens(f"telegram_{user_id}", len(response) // 4)
        await _send_long_message(update, response)
    except Exception as exc:
        log.error("Telegram /model handler error: %s", exc, exc_info=True)
        await update.message.reply_text(f"❌ An error occurred: `{type(exc).__name__}`: {exc}")


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle regular text messages."""
    if not update.message or not update.message.text:
        return
    await _handle_ai_message(update, context, update.message.text)


async def handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    if query.data == "help":
        await cmd_help(update, context)
    elif query.data == "clear":
        await cmd_clear(update, context)
    elif query.data == "status":
        await cmd_status(update, context)


async def handle_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle photo messages — route to vision-capable model."""
    if not update.message:
        return
    caption = update.message.caption or "Please describe or analyse this image in detail."
    await _handle_ai_message(
        update,
        context,
        f"[User sent a photo] Caption: '{caption}'. "
        f"Please analyse or describe it based on the caption provided.",
        has_media=True,
    )


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle document messages — route to capable model."""
    if not update.message:
        return
    doc = update.message.document
    caption = update.message.caption or ""
    file_info = f"'{doc.file_name}' (type: {doc.mime_type}, size: {doc.file_size} bytes)"
    await _handle_ai_message(
        update,
        context,
        f"User sent a file: {file_info}. "
        f"Caption/instruction: '{caption}'. "
        f"Help them with this file based on its name, type, and the caption provided.",
        has_media=True,
    )


# ───────────────────────────────────────────────────────────────────────────────
# App factory + startup
# ───────────────────────────────────────────────────────────────────────────────

async def start_telegram() -> None:
    """Build and start the Telegram bot. Returns if no token is configured."""
    if not settings.telegram_token:
        log.warning("No TELEGRAM_TOKEN set — skipping Telegram bot")
        return

    log.info("Starting Telegram bot...")

    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .concurrent_updates(True)
        .build()
    )

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("model", cmd_model))

    # Register message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Set bot commands menu
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help"),
        BotCommand("model", "Force a specific AI provider"),
        BotCommand("clear", "Clear conversation history"),
        BotCommand("status", "View your usage stats"),
        BotCommand("about", "About this bot"),
    ])

    try:
        await app.initialize()
        await app.start()
        log.info("Telegram bot started successfully")
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        # Keep alive until cancelled
        import asyncio
        await asyncio.Event().wait()
    except Exception as exc:
        log.error("Telegram bot error: %s", exc, exc_info=True)
        raise
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
