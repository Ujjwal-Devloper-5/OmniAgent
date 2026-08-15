import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import ast
import operator
import math
from adapters.discord_bot import parse_duration

_POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

class UtilCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Create an interactive poll with emoji reactions")
    @app_commands.describe(
        question="The poll question",
        options="Options separated by | (e.g. Yes | No | Maybe)",
    )
    async def slash_poll(self, interaction: discord.Interaction, question: str, options: str) -> None:
        await interaction.response.defer()
        choices = [o.strip() for o in options.split("|") if o.strip()]

        if len(choices) < 2:
            await interaction.followup.send("❌ Please provide at least 2 options separated by `|`", ephemeral=True)
            return
        if len(choices) > 10:
            await interaction.followup.send("❌ Maximum 10 options allowed.", ephemeral=True)
            return

        options_text = "\n".join(
            f"{_POLL_EMOJIS[i]}  {choice}"
            for i, choice in enumerate(choices)
        )
        embed = discord.Embed(
            title=f"📊 {question}",
            description=options_text,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Poll by {interaction.user.display_name} • React to vote!")

        poll_msg = await interaction.followup.send(embed=embed)
        try:
            fetched = await interaction.channel.fetch_message(poll_msg.id)
            for i in range(len(choices)):
                await fetched.add_reaction(_POLL_EMOJIS[i])
        except Exception:
            pass

    @app_commands.command(name="remind", description="Set a personal reminder")
    @app_commands.describe(
        duration="When to remind you (e.g. 10m, 2h, 1d)",
        message="What to remind you about",
    )
    async def slash_remind(self, interaction: discord.Interaction, duration: str, message: str) -> None:
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message(
                "❌ Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d`.",
                ephemeral=True,
            )
            return
        if seconds > 86400 * 7:
            await interaction.response.send_message("❌ Maximum reminder duration is 7 days.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"⏰ Got it! I'll remind you about **{message}** in **{duration}**.",
            ephemeral=True,
        )

        async def _fire_reminder():
            await asyncio.sleep(seconds)
            try:
                embed = discord.Embed(
                    title="⏰ Reminder!",
                    description=message,
                    color=discord.Color.yellow(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text=f"You asked me to remind you {duration} ago.")
                await interaction.user.send(embed=embed)
            except discord.Forbidden:
                try:
                    await interaction.channel.send(f"⏰ {interaction.user.mention} Reminder: **{message}**")
                except Exception:
                    pass

        asyncio.create_task(_fire_reminder())

    @app_commands.command(name="avatar", description="Get a user's avatar")
    @app_commands.describe(user="The user whose avatar you want")
    async def slash_avatar(self, interaction: discord.Interaction, user: discord.Member = None) -> None:
        target = user or interaction.user
        embed = discord.Embed(
            title=f"{target.display_name}'s Avatar",
            color=target.color if target.color != discord.Color.default() else discord.Color.blurple()
        )
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="calculate", description="Calculate a math expression")
    @app_commands.describe(expression="The math expression to evaluate")
    async def slash_calculate(self, interaction: discord.Interaction, expression: str) -> None:
        if len(expression) > 100:
            await interaction.response.send_message("❌ Expression too long (max 100 chars).", ephemeral=True)
            return

        _OPERATORS = {
            ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos
        }
        
        def _safe_pow(a, b):
            if abs(b) > 100: raise ValueError("Exponent too large")
            return operator.pow(a, b)
        _OPERATORS[ast.Pow] = _safe_pow

        def eval_expr(node):
            if isinstance(node, ast.Num): return node.n
            elif isinstance(node, ast.BinOp):
                return _OPERATORS[type(node.op)](eval_expr(node.left), eval_expr(node.right))
            elif isinstance(node, ast.UnaryOp):
                return _OPERATORS[type(node.op)](eval_expr(node.operand))
            else:
                raise TypeError("Unsupported operation")

        try:
            tree = ast.parse(expression, mode='eval').body
            result = eval_expr(tree)
            await interaction.response.send_message(f"🧮 **{expression}** = `{result}`")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error evaluating expression: {e}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilCog(bot))
