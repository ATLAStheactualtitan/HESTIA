import discord


def setup_slash_commands(bot):
    @bot.tree.command(name="ping", description="Check the bot latency")
    async def ping_command(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Pong! Latency: {round(bot.latency * 1000)}ms",
            ephemeral=True,
        )

    @bot.tree.command(name="status", description="Check bot and API health")
    async def status_command(interaction: discord.Interaction):
        api_client = getattr(bot, "api_client", None)
        api_status = "not configured"

        if api_client and api_client.base_url:
            try:
                result = await api_client.get_json("/health")
                api_status = "online" if result else "unknown"
            except Exception:
                api_status = "error"

        await interaction.response.send_message(
            f"Bot: online\nAPI: {api_status}",
            ephemeral=True,
        )

    return bot
