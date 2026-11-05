"""/settings: lets server administrators configure the stockpile features."""

from typing import Optional

import discord
from discord import app_commands

from .config import delete_guild_config, get_guild_config, set_guild_config
from .feature import (
    BRAND_COLOR,
    BRAND_NAME,
    STOCKPILE_DEFAULT_ROLE_KEY,
    STOCKPILE_REMINDER_CHANNEL_KEY,
    STOCKPILE_REMINDER_ENABLED_KEY,
    get_configured_stockpile_role_ids,
    get_default_stockpile_role_id,
)

ADMIN_ROLES_KEY = "stockpile_access_roles"  # holds the Staff role; older multi-role values still work


def _role_label(guild: discord.Guild, role_id: int) -> str:
    return "@everyone" if role_id == guild.id else f"<@&{role_id}>"


def _build_settings_embed(guild: discord.Guild) -> discord.Embed:
    admin_ids = [role_id for role_id in get_configured_stockpile_role_ids(guild.id) if guild.get_role(role_id)]
    channel = guild.get_channel(int(get_guild_config(guild.id, STOCKPILE_REMINDER_CHANNEL_KEY) or 0))
    reminders_on = bool(get_guild_config(guild.id, STOCKPILE_REMINDER_ENABLED_KEY, False))

    embed = discord.Embed(title="Stockpile settings", color=BRAND_COLOR)
    embed.set_author(name=BRAND_NAME)
    embed.add_field(
        name="Default access role",
        value=f"{_role_label(guild, get_default_stockpile_role_id(guild))}\nCan add stockpiles and see new ones in `/stockpile-list`.",
        inline=False,
    )
    embed.add_field(
        name="Staff role",
        value=(
            ", ".join(f"<@&{role_id}>" for role_id in admin_ids)
            or "Not set (server administrators only)"
        ) + "\nCan use `/stockpile-edit` and `/stockpile-remove`. Server administrators always can.",
        inline=False,
    )
    embed.add_field(
        name="Expiry reminders",
        value=(
            f"{'Enabled' if reminders_on else 'Disabled'} | Channel: "
            f"{channel.mention if isinstance(channel, discord.TextChannel) else 'Not set'}"
        ),
        inline=False,
    )
    return embed


@app_commands.command(name="settings", description="Admin: view or change the stockpile settings")
@app_commands.describe(
    default_role="Role that can add stockpiles and sees new stockpiles by default",
    staff_role="Role that can use /stockpile-edit and /stockpile-remove",
    clear_staff_role="Remove the Staff role (only server administrators keep access)",
    reminders="Turn the 36h stockpile expiry reminders on or off",
    reminder_channel="Channel that receives the expiry reminders",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def settings_command(
    interaction: discord.Interaction,
    default_role: Optional[discord.Role] = None,
    staff_role: Optional[discord.Role] = None,
    clear_staff_role: Optional[bool] = None,
    reminders: Optional[bool] = None,
    reminder_channel: Optional[discord.TextChannel] = None,
):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only server administrators can change settings.", ephemeral=True)
        return

    notes: list[str] = []

    if reminder_channel is not None:
        permissions = reminder_channel.permissions_for(guild.me)
        if not (permissions.send_messages and permissions.embed_links):
            await interaction.response.send_message(
                f"I need Send Messages and Embed Links in {reminder_channel.mention}.", ephemeral=True
            )
            return

    if default_role is not None:
        set_guild_config(guild.id, STOCKPILE_DEFAULT_ROLE_KEY, str(default_role.id))
        notes.append("Default access role updated. Existing stockpiles keep their current role; change those with `/stockpile-edit`.")
    if clear_staff_role:
        delete_guild_config(guild.id, ADMIN_ROLES_KEY)
        notes.append("Staff role cleared.")
    if staff_role is not None:
        set_guild_config(guild.id, ADMIN_ROLES_KEY, [str(staff_role.id)])
        notes.append("Staff role updated.")
    if reminder_channel is not None:
        set_guild_config(guild.id, STOCKPILE_REMINDER_CHANNEL_KEY, str(reminder_channel.id))
        notes.append("Reminder channel updated.")
    if reminders is not None:
        set_guild_config(guild.id, STOCKPILE_REMINDER_ENABLED_KEY, reminders)
        notes.append(f"Reminders {'enabled' if reminders else 'disabled'}.")
    if (reminders or (reminders is None and get_guild_config(guild.id, STOCKPILE_REMINDER_ENABLED_KEY, False))) and not get_guild_config(guild.id, STOCKPILE_REMINDER_CHANNEL_KEY):
        notes.append("Reminders are on but no reminder channel is set, so none will be sent.")

    await interaction.response.send_message(
        content="\n".join(notes) or None,
        embed=_build_settings_embed(guild),
        ephemeral=True,
    )
