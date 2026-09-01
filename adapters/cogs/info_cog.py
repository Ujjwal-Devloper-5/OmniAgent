import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from datetime import datetime, timezone
import platform as platform_module

from config import settings
from core.rate_limiter import get_rate_limiter
from core.agent import get_status

class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="userinfo", description="Show detailed info about a user")
    @app_commands.describe(user="The user to look up (leave blank for yourself)")
    async def slash_userinfo(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        target = user or interaction.user
        member = interaction.guild.get_member(target.id) if interaction.guild else None

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            color=target.color if member and target.color != discord.Color.default() else discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🆔 User ID", value=str(target.id), inline=True)
        embed.add_field(name="🤖 Bot", value="Yes" if target.bot else "No", inline=True)
        embed.add_field(
            name="📅 Account Created",
            value=f"<t:{int(target.created_at.timestamp())}:R>",
            inline=True,
        )
        if member:
            join_str = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown"
            embed.add_field(name="📥 Joined Server", value=join_str, inline=True)
            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
            embed.add_field(
                name=f"🎭 Roles ({len(roles)})",
                value=" ".join(roles[:10]) or "None",
                inline=False,
            )
        embed.set_footer(text="OmniAgent • Created by Ujjwal Kumar")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Show server statistics and info")
    async def slash_serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This command only works in servers.", ephemeral=True)
            return

        bots    = sum(1 for m in guild.members if m.bot)
        humans  = guild.member_count - bots
        text_ch = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        online  = sum(1 for m in guild.members if m.status != discord.Status.offline)

        embed = discord.Embed(
            title=f"🏠 {guild.name}",
            description=guild.description or "No description set.",
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.now(timezone.utc),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        owner_mention = guild.owner.mention if guild.owner else "Unknown"
        embed.add_field(name="👑 Owner", value=owner_mention, inline=True)
        embed.add_field(name="🆔 Server ID", value=str(guild.id), inline=True)
        embed.add_field(
            name="📅 Created",
            value=f"<t:{int(guild.created_at.timestamp())}:R>",
            inline=True,
        )
        embed.add_field(
            name="👥 Members",
            value=f"**Total:** {guild.member_count}\n**Humans:** {humans}\n**Bots:** {bots}\n**Online:** ~{online}",
            inline=True,
        )
        embed.add_field(
            name="💬 Channels",
            value=f"**Text:** {text_ch}\n**Voice:** {voice_ch}\n**Categories:** {len(guild.categories)}",
            inline=True,
        )
        embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(
            name="🔒 Verification",
            value=str(guild.verification_level).replace("_", " ").title(),
            inline=True,
        )
        embed.add_field(
            name="🚀 Boost Status",
            value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)",
            inline=True,
        )
        embed.set_footer(text="OmniAgent • Created by Ujjwal Kumar")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="status", description="Show bot status and AI provider health")
    async def slash_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        uptime   = datetime.now(timezone.utc) - self.bot._start_time
        hours, r = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(r, 60)
        stats    = await get_rate_limiter().get_stats(f"discord_{interaction.user.id}")
        provider_health = await get_status()

        embed = discord.Embed(
            title=f"🤖 {settings.bot_name} — System Status",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="⏱️ Uptime",    value=f"{hours}h {minutes}m {seconds}s", inline=True)
        embed.add_field(name="💬 Processed", value=str(self.bot._messages_processed),       inline=True)
        embed.add_field(name="🐍 Python",    value=platform_module.python_version(),   inline=True)

        icons     = {"gemini": "🟡", "openai": "🟢", "anthropic": "🟣", "groq": "⚡", "openrouter": "🆓", "ollama": "🔵"}
        free_tags = {"groq": " (FREE)", "openrouter": " (FREE)", "ollama": " (local)"}
        lines     = []
        for pname, info in provider_health.items():
            icon  = icons.get(pname, "⚪")
            tag   = free_tags.get(pname, "")
            if info["configured"] and info["healthy"]:
                s = f"✅ Online{tag}"
            elif info["configured"]:
                s = f"❌ Unhealthy ({info['consecutive_failures']} fails)"
            else:
                s = "⚫ Not configured"
            lines.append(f"{icon} **{pname.capitalize()}**: {s}")

        embed.add_field(name="🤖 AI Providers", value="\n".join(lines), inline=False)
        embed.add_field(
            name="⚡ Your Rate Limit",
            value=(
                f"Requests: {stats['requests_remaining_this_minute']}/{stats['requests_per_minute_limit']}/min\n"
                f"Tokens: {stats['tokens_used_today']:,}/{stats['tokens_per_day_limit']:,} today\n"
                f"Active users tracked: {stats.get('active_users_tracked', '?')}"
            ),
            inline=False,
        )

        # ── Dynamic model pool from ModelRegistry ──────────────────────────────
        try:
            from core.model_registry import get_registry
            reg = get_registry()
            if reg.available_count > 0:
                summary = reg.get_registry_summary()
                # Discord field values are capped at 1024 characters
                if len(summary) > 1020:
                    summary = summary[:1020] + "…"
                embed.add_field(
                    name=f"🧠 Model Pool ({reg.available_count}/{reg.total_models} available)",
                    value=summary,
                    inline=False,
                )
        except Exception:
            pass  # Registry not yet initialised — silently skip

        embed.set_footer(text="Created by Ujjwal Kumar • OmniAgent v3.1")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="help", description="Show all available commands")
    async def slash_help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"🤖 {settings.bot_name} — Command Guide",
            description=(
                "An advanced multi-agent AI assistant with full server awareness.\n"
                "**Created by Ujjwal Kumar**\n\n"
                "**How to chat:** Mention me `@OmniAgent` or slide into my DMs! I'll understand your language automatically. 🌍"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="💬 AI Commands",
            value=(
                "`/ask <question>` — Ask the AI anything\n"
                "`/model <provider> <question>` — Force a specific AI model\n"
                "`/translate <text> <language>` — Translate to any language\n"
                "`/clear` — Reset your conversation history"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Server Tools",
            value=(
                "`/summarize [count]` — AI summarises recent channel messages\n"
                "`/poll <question> | opt1 | opt2` — Create a reaction poll\n"
                "`/topic` — AI generates a channel topic from recent chat\n"
                "`/announce <title> <msg>` — Rich announcement (Admin)\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Moderation",
            value=(
                "`/purge <count>` — Delete messages\n"
                "`/kick <user>` — Kick a member\n"
                "`/ban <user>` — Ban a member"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏰ Utilities & Fun",
            value=(
                "`/remind <duration> <message>` — Set a personal reminder\n"
                "`/calculator <expr>` — Safe math calculations\n"
                "`/roll <dice>` — Roll dice (e.g. 2d6)\n"
                "`/8ball <question>` — Ask the magic 8-ball\n"
                "`/coinflip` — Heads or tails"
            ),
            inline=False,
        )
        embed.add_field(
            name="👤 Info Commands",
            value=(
                "`/userinfo [user]` — Detailed user profile\n"
                "`/serverinfo` — Full server statistics\n"
                "`/avatar [user]` — Show user's avatar\n"
                "`/status` — AI provider health & rate limits\n"
                "`/help` — This menu"
            ),
            inline=False,
        )
        embed.set_footer(text="I remember your full conversation • Created by Ujjwal Kumar")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InfoCog(bot))
