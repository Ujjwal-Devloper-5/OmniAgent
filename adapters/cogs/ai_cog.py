import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from core.agent import clear_memory, process_message
from core.rate_limiter import get_rate_limiter
from adapters.discord_bot import fetch_channel_context

class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ask", description="Ask the AI a question")
    @app_commands.describe(question="Your question or task for the AI")
    async def slash_ask(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.defer(thinking=True)
        await self.bot.handle_ai_request(
            message=None,
            content=question,
            user_id=str(interaction.user.id),
            interaction=interaction,
        )

    @app_commands.command(name="model", description="Force a specific AI provider")
    @app_commands.describe(provider="AI provider to use", question="Your question")
    @app_commands.choices(provider=[
        app_commands.Choice(name="🟡 Gemini Flash (Google)", value="gemini"),
        app_commands.Choice(name="🟢 GPT-4o (OpenAI)", value="openai"),
        app_commands.Choice(name="🟣 Claude Sonnet (Anthropic)", value="anthropic"),
        app_commands.Choice(name="⚡ Groq — FREE ultra-fast", value="groq"),
        app_commands.Choice(name="🆓 OpenRouter — FREE models", value="openrouter"),
        app_commands.Choice(name="🔵 Ollama (Local / Offline)", value="ollama"),
    ])
    async def slash_model(self, interaction: discord.Interaction, provider: str, question: str) -> None:
        await interaction.response.defer(thinking=True)
        await self.bot.handle_ai_request(
            message=None,
            content=question,
            user_id=str(interaction.user.id),
            interaction=interaction,
            force_provider=provider,
        )

    @app_commands.command(name="summarize", description="AI summarises recent channel messages")
    @app_commands.describe(count="Number of messages to summarise (default 30, max 100)")
    async def slash_summarize(self, interaction: discord.Interaction, count: int = 30) -> None:
        await interaction.response.defer(thinking=True)
        count = max(5, min(count, 100))
        rate_limiter = get_rate_limiter()
        allowed, reason = await rate_limiter.check_request(f"discord_{interaction.user.id}")
        if not allowed:
            await interaction.followup.send(reason, ephemeral=True)
            return

        channel = interaction.channel
        if not hasattr(channel, "history"):
            await interaction.followup.send("❌ Can't read history in this channel type.", ephemeral=True)
            return

        context = await fetch_channel_context(channel, limit=count)
        if not context:
            await interaction.followup.send("📭 No messages to summarise.", ephemeral=True)
            return

        prompt = (
            f"Please create a clear, well-structured summary of the following Discord channel conversation. "
            f"Highlight: main topics discussed, key decisions made, any questions left unanswered, "
            f"and the general mood/tone. Format nicely with headers and bullet points.\n\n{context}"
        )
        try:
            response = await process_message(
                session_id=f"discord_{interaction.user.id}_summary",
                message=prompt,
                platform="discord",
            )
            embed = discord.Embed(
                title=f"📋 Channel Summary — Last {count} Messages",
                description=response[:4000],
                color=discord.Color.teal(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name} • Powered by AI")
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Summary failed: {exc}", ephemeral=True)

    @app_commands.command(name="topic", description="AI generates a topic for this channel based on recent chat")
    async def slash_topic(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if not interaction.guild:
            await interaction.followup.send("❌ Server only command.", ephemeral=True)
            return

        context = await fetch_channel_context(interaction.channel, limit=30)
        if not context:
            await interaction.followup.send("📭 Not enough messages to generate a topic.", ephemeral=True)
            return

        prompt = (
            f"Based on this Discord channel conversation, write a single short engaging channel topic "
            f"(max 100 characters, no emojis in topic itself). Just output the topic text only.\n\n{context}"
        )
        try:
            response = await process_message(
                session_id=f"discord_{interaction.user.id}_topic",
                message=prompt,
                platform="discord",
            )
            topic = response.strip()[:100]
            embed = discord.Embed(
                title="💡 AI-Generated Channel Topic",
                description=f"**Suggested topic:**\n> {topic}",
                color=discord.Color.teal(),
            )
            embed.set_footer(text="Use /announce or manually update the channel topic in settings")
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Topic generation failed: {exc}", ephemeral=True)

    @app_commands.command(name="translate", description="Translate text to any language using AI")
    @app_commands.describe(
        text="The text to translate",
        language="Target language (e.g. Hindi, Spanish, French, Japanese)",
    )
    async def slash_translate(self, interaction: discord.Interaction, text: str, language: str) -> None:
        await interaction.response.defer(thinking=True)
        rate_limiter = get_rate_limiter()
        allowed, reason = await rate_limiter.check_request(f"discord_{interaction.user.id}")
        if not allowed:
            await interaction.followup.send(reason, ephemeral=True)
            return
        try:
            prompt = (
                f"Translate the following text to {language}. "
                f"Only output the translated text, nothing else.\n\nText: {text}"
            )
            response = await process_message(
                session_id=f"discord_{interaction.user.id}_translate",
                message=prompt,
                platform="discord",
            )
            embed = discord.Embed(color=discord.Color.blue())
            embed.add_field(name="📝 Original", value=text[:1000], inline=False)
            embed.add_field(name=f"🌐 {language}", value=response[:1000], inline=False)
            embed.set_footer(text=f"Translated by AI • Requested by {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Translation failed: {exc}", ephemeral=True)

    @app_commands.command(name="clear", description="Clear your AI conversation history")
    async def slash_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await clear_memory(f"discord_{interaction.user.id}")
            await interaction.followup.send("🗑️ Your conversation history has been cleared!", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Failed to clear history: {exc}", ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
