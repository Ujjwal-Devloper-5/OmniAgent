"""
Advanced Discord bot adapter — Production Grade v3.1
Core module for Discord Bot that loads extensions (Cogs) for modular commands.
"""
from __future__ import annotations

import asyncio
import platform as platform_module
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import settings
from core.agent import clear_memory, get_status, get_task_classification, process_message
from core.helpers import split_message
from core.logger import get_logger
from core.rate_limiter import get_rate_limiter

log = get_logger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────
_CONTEXT_MESSAGES   = 20    # How many channel messages to read for context
_ACTIVITY_ROTATION  = [
    ("listening",  "your questions | /help"),
    ("watching",   "over this server 👀"),
    ("playing",    "with AI models 🤖"),
    ("listening",  "/ask | /summarize | /poll"),
    ("watching",   "for @mentions"),
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def fetch_channel_context(
    channel: discord.TextChannel | discord.Thread | discord.VoiceChannel,
    limit: int = _CONTEXT_MESSAGES,
    before: Optional[discord.Message] = None,
) -> str:
    """
    Fetch the last `limit` messages from a channel and format them
    as a context block for the AI to understand what's being discussed.
    """
    try:
        history = []
        kwargs = {"limit": limit}
        if before:
            kwargs["before"] = before
        async for msg in channel.history(**kwargs):
            if msg.author.bot:
                continue
            ts = msg.created_at.strftime("%H:%M")
            history.append(f"[{ts}] {msg.author.display_name}: {msg.content}")
        history.reverse()
        if not history:
            return ""
        return "--- Channel Context (recent messages) ---\n" + "\n".join(history) + "\n--- End Context ---"
    except Exception:
        return ""


def make_ai_prompt_with_context(user_message: str, context: str, author: discord.Member | discord.User) -> str:
    """
    Build a rich prompt that includes channel context, user info,
    and a language-detection instruction so the AI responds naturally.
    """
    lang_instruction = (
        "IMPORTANT: Detect the language of the user's message and respond in that EXACT same language. "
        "If they write in Hindi, respond in Hindi. If English, respond in English. Match their language perfectly."
    )
    user_info = f"User: {author.display_name} (ID: {author.id})"

    parts = [lang_instruction, user_info]
    if context:
        parts.append(context)
    parts.append(f"User's question/message: {user_message}")

    return "\n\n".join(parts)


def parse_duration(text: str) -> int | None:
    """
    Parse a human duration string into seconds.
    Examples: '10s', '5m', '2h', '1d'
    Returns None if unparseable.
    """
    match = re.fullmatch(r"(\d+)\s*(s|sec|second|m|min|minute|h|hour|d|day)s?", text.strip().lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)[0]
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


# ─── Bot Class ────────────────────────────────────────────────────────────────

class OmniAgentDiscord(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members          = True
        intents.presences        = False  # Not needed, saves resources
        super().__init__(
            command_prefix=settings.bot_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )
        self._start_time        = datetime.now(timezone.utc)
        self._messages_processed = 0
        self._activity_index    = 0

    async def setup_hook(self) -> None:
        log.info("Loading extensions...")
        extensions = [
            "adapters.cogs.ai_cog",
            "adapters.cogs.mod_cog",
            "adapters.cogs.util_cog",
            "adapters.cogs.info_cog",
            "adapters.cogs.fun_cog",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                log.info(f"Loaded extension: {ext}")
            except Exception as e:
                log.error("Failed to load extension %s: %s", ext, e, exc_info=True)
                
        log.info("Registering Discord slash commands...")
        await self.tree.sync()
        log.info("Slash commands synced.")
        self._rotate_status.start()

    async def on_ready(self) -> None:
        log.info(
            "Discord bot ready | user=%s id=%s guilds=%d",
            self.user,
            self.user.id if self.user else "?",
            len(self.guilds),
        )

    async def on_disconnect(self) -> None:
        log.warning("Discord bot disconnected")

    async def on_resume(self) -> None:
        log.info("Discord bot connection resumed")

    # ── Activity Status Rotation ──────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def _rotate_status(self) -> None:
        """Cycle through activity statuses every 5 minutes."""
        atype_name, text = _ACTIVITY_ROTATION[self._activity_index % len(_ACTIVITY_ROTATION)]
        atype_map = {
            "listening": discord.ActivityType.listening,
            "watching":  discord.ActivityType.watching,
            "playing":   discord.ActivityType.playing,
        }
        activity = discord.Activity(type=atype_map[atype_name], name=text)
        await self.change_presence(status=discord.Status.online, activity=activity)
        self._activity_index += 1

    @_rotate_status.before_loop
    async def _before_rotate(self) -> None:
        await self.wait_until_ready()

    # ── Welcome New Members ───────────────────────────────────────────────────

    async def on_member_join(self, member: discord.Member) -> None:
        """Send a personalised welcome embed when someone joins the server."""
        # Find a suitable channel to welcome in
        channel = (
            member.guild.system_channel
            or next(
                (
                    c for c in member.guild.text_channels
                    if any(k in c.name.lower() for k in ["welcome", "general", "lobby", "chat"])
                    and c.permissions_for(member.guild.me).send_messages
                ),
                None,
            )
        )
        if not channel:
            return

        embed = discord.Embed(
            title=f"👋 Welcome to {member.guild.name}!",
            description=(
                f"Hey {member.mention}, we're thrilled to have you here! 🎉\n\n"
                f"**{member.guild.name}** now has **{member.guild.member_count}** members.\n\n"
                f"Feel free to explore the server, introduce yourself, and don't hesitate to ask me anything — "
                f"just **mention me** or slide into my DMs!"
            ),
            color=discord.Color.from_rgb(88, 101, 242),  # Discord blurple
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="🤖 I'm your AI Assistant",
            value=f"Mention me `@{self.user.name}` or use `/help` to see what I can do!",
            inline=False,
        )
        embed.set_footer(text=f"Member #{member.guild.member_count} • Created by Ujjwal Kumar")
        try:
            await channel.send(embed=embed)
            log.info("Welcome message sent for %s in %s", member, member.guild)
        except Exception as exc:
            log.warning("Failed to send welcome message: %s", exc)

    # ── Core Message Handler ──────────────────────────────────────────────────

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or message.author.bot:
            return
        await self.process_commands(message)

        is_mention = self.user and self.user in message.mentions
        is_dm      = isinstance(message.channel, discord.DMChannel)

        if not (is_mention or is_dm):
            return

        # Strip bot mention from content
        content = message.content
        if self.user:
            content = (
                content
                .replace(f"<@{self.user.id}>", "")
                .replace(f"<@!{self.user.id}>", "")
                .strip()
            )

        if not content:
            replies = [
                f"Hey {message.author.display_name}! 👋 What can I help you with? Try `/help` to see all my features!",
                f"You called? 😄 Ask me anything, {message.author.display_name}!",
                f"Hi {message.author.display_name}! 🤖 I'm listening — what's on your mind?",
            ]
            import random
            await message.reply(random.choice(replies))
            return

        # Fetch channel context for better AI understanding (skip for DMs)
        context = ""
        if not is_dm and hasattr(message.channel, "history"):
            context = await fetch_channel_context(
                message.channel,
                limit=_CONTEXT_MESSAGES,
                before=message,
            )

        # Build enriched prompt with context + language detection
        enriched_content = make_ai_prompt_with_context(content, context, message.author)

        await self.handle_ai_request(
            message=message,
            content=enriched_content,
            user_id=str(message.author.id),
            reply_to=message,
        )

    # ── Core AI Request Handler ───────────────────────────────────────────────

    async def handle_ai_request(
        self,
        message: Optional[discord.Message],
        content: str,
        user_id: str,
        reply_to: Optional[discord.Message] = None,
        interaction: Optional[discord.Interaction] = None,
        force_provider: Optional[str] = None,
    ) -> None:
        rate_limiter = get_rate_limiter()
        allowed, reason = await rate_limiter.check_request(f"discord_{user_id}")
        if not allowed:
            target = interaction or reply_to
            if interaction:
                await interaction.followup.send(reason, ephemeral=True)
            elif reply_to:
                await reply_to.reply(reason)
            return

        session_id = f"discord_{user_id}"
        channel = (
            message.channel
            if message
            else (interaction.channel if interaction else None)
        )
        if channel is None:
            return

        try:
            # We can't use channel.typing() easily if we just have an interaction channel which might be a partial
            # But normally interaction.channel supports typing if it's resolved. Let's just try.
            async with channel.typing():
                response = await process_message(
                    session_id, 
                    content, 
                    platform="discord",
                    force_provider=force_provider
                )

            self._messages_processed += 1
            await rate_limiter.record_tokens(f"discord_{user_id}", len(response) // 4)

            chunks = split_message(response, max_length=1950)
            for i, chunk in enumerate(chunks):
                if i == 0 and reply_to:
                    await reply_to.reply(chunk)
                elif interaction:
                    await interaction.followup.send(chunk)
                elif reply_to:
                    await reply_to.channel.send(chunk)

        except discord.Forbidden:
            log.error("Missing permissions in channel %s", channel)
        except Exception as exc:
            log.error("Discord AI handler error: %s", exc, exc_info=True)
            err_msg = f"⚠️ Something went wrong: `{type(exc).__name__}`. Please try again."
            try:
                if interaction:
                    await interaction.followup.send(err_msg, ephemeral=True)
                elif reply_to:
                    await reply_to.reply(err_msg)
            except Exception:
                pass

    async def on_command_error(self, ctx, error) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        log.error("Command error in %s: %s", ctx.command, error)


_bot_instance: OmniAgentDiscord | None = None

def get_discord_bot() -> OmniAgentDiscord:
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = OmniAgentDiscord()
    return _bot_instance


async def start_discord() -> None:
    if not settings.discord_token:
        log.warning("No DISCORD_TOKEN set - skipping Discord bot")
        return
    bot = get_discord_bot()
    log.info("Starting Discord bot...")
    try:
        await bot.start(settings.discord_token)
    except discord.LoginFailure:
        log.error("Discord login failed: invalid token")
    except Exception as exc:
        log.error("Discord bot crashed: %s", exc, exc_info=True)
        raise
