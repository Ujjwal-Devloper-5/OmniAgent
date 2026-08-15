import discord
from discord import app_commands
from discord.ext import commands
import random
import re

class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="coinflip", description="Flip a coin")
    async def slash_coinflip(self, interaction: discord.Interaction) -> None:
        result = random.choice(["Heads", "Tails"])
        emoji = "🪙" if result == "Heads" else "🪙"
        await interaction.response.send_message(f"{emoji} It's **{result}**!")

    @app_commands.command(name="roll", description="Roll a dice (e.g. 1d6, 2d20)")
    @app_commands.describe(dice="Dice format (e.g. 2d6)")
    async def slash_roll(self, interaction: discord.Interaction, dice: str = "1d6") -> None:
        match = re.fullmatch(r"(\d+)d(\d+)", dice.lower().strip())
        if not match:
            await interaction.response.send_message("❌ Invalid format! Use something like `1d6` or `2d20`.", ephemeral=True)
            return
        
        count = int(match.group(1))
        sides = int(match.group(2))

        if count < 1 or count > 50:
            await interaction.response.send_message("❌ Dice count must be between 1 and 50.", ephemeral=True)
            return
        if sides < 2 or sides > 1000:
            await interaction.response.send_message("❌ Dice sides must be between 2 and 1000.", ephemeral=True)
            return

        rolls = [random.randint(1, sides) for _ in range(count)]
        total = sum(rolls)
        
        if count == 1:
            await interaction.response.send_message(f"🎲 Rolled a **d{sides}**: `{total}`")
        else:
            rolls_str = ", ".join(map(str, rolls))
            await interaction.response.send_message(f"🎲 Rolled **{count}d{sides}**: `{rolls_str}`\n**Total:** `{total}`")

    @app_commands.command(name="8ball", description="Ask the Magic 8-Ball a question")
    @app_commands.describe(question="The question to ask")
    async def slash_8ball(self, interaction: discord.Interaction, question: str) -> None:
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.",
            "Yes definitely.", "You may rely on it.", "As I see it, yes.",
            "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
            "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.",
            "Outlook not so good.", "Very doubtful."
        ]
        answer = random.choice(responses)
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            color=discord.Color.dark_theme(),
        )
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=answer, inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
