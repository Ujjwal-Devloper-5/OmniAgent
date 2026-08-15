import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from datetime import datetime, timezone

class ModCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="announce", description="[Admin] Make a rich server announcement")
    @app_commands.describe(
        title="Announcement title",
        message="Announcement body",
        channel="Target channel (defaults to current channel)",
        color="Embed color: red | green | blue | gold | blurple (default)",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_announce(
        self,
        interaction: discord.Interaction,
        title: str,
        message: str,
        channel: Optional[discord.TextChannel] = None,
        color: str = "blurple",
    ) -> None:
        target_channel = channel or interaction.channel
        color_map = {
            "red":     discord.Color.red(),
            "green":   discord.Color.green(),
            "blue":    discord.Color.blue(),
            "gold":    discord.Color.gold(),
            "blurple": discord.Color.blurple(),
        }
        embed_color = color_map.get(color.lower(), discord.Color.blurple())
        embed = discord.Embed(
            title=f"📢 {title}",
            description=message,
            color=embed_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(
            text=f"Announcement by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
        )
        try:
            await target_channel.send(embed=embed)
            await interaction.response.send_message(f"✅ Announcement sent to {target_channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ No permission to post in {target_channel.mention}.", ephemeral=True)
        except Exception as exc:
            await interaction.response.send_message(f"⚠️ Failed to send announcement: {exc}", ephemeral=True)

    @slash_announce.error
    async def announce_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Manage Messages** permission to use this.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ An error occurred: {error}", ephemeral=True)

    @app_commands.command(name="purge", description="[Admin] Delete a number of recent messages")
    @app_commands.describe(amount="Number of messages to delete (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_purge(self, interaction: discord.Interaction, amount: int) -> None:
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Please provide a number between 1 and 100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to manage messages.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Failed to purge messages: {exc}", ephemeral=True)

    @slash_purge.error
    async def purge_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Manage Messages** permission to use this.", ephemeral=True)

    @app_commands.command(name="kick", description="[Admin] Kick a member from the server")
    @app_commands.describe(member="Member to kick", reason="Reason for kicking")
    @app_commands.checks.has_permissions(kick_members=True)
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided") -> None:
        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(f"👢 Kicked {member.mention} for: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to kick this user.", ephemeral=True)

    @slash_kick.error
    async def kick_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Kick Members** permission to use this.", ephemeral=True)

    @app_commands.command(name="ban", description="[Admin] Ban a member from the server")
    @app_commands.describe(member="Member to ban", reason="Reason for banning", delete_message_days="Days of messages to delete")
    @app_commands.checks.has_permissions(ban_members=True)
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_message_days: int = 0) -> None:
        if not (0 <= delete_message_days <= 7):
            await interaction.response.send_message("❌ delete_message_days must be between 0 and 7.", ephemeral=True)
            return
        try:
            await member.ban(reason=reason, delete_message_days=delete_message_days)
            await interaction.response.send_message(f"🔨 Banned {member.mention} for: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to ban this user.", ephemeral=True)

    @slash_ban.error
    async def ban_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Ban Members** permission to use this.", ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModCog(bot))
