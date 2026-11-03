import discord


def setup(bot):
    @bot.tree.command(name="module-ping", description="Ping the bot from a module")
    async def module_ping(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Pong! Latency: {round(bot.latency * 1000)}ms",
            ephemeral=True,
        )

    @bot.tree.command(name="module-hello", description="Hello from a module")
    async def module_hello(interaction: discord.Interaction):
        await interaction.response.send_message("Hello from a module!", ephemeral=True)
