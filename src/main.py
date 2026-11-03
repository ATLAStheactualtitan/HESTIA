from pathlib import Path

from bot import ModularBot
from config import DISCORD_TOKEN
from slash_commands import setup_slash_commands


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it to a .env file or your environment variables."
        )

    bot = ModularBot(
        command_prefix="",
        module_dir=Path(__file__).resolve().parent / "modules",
    )

    setup_slash_commands(bot)
    bot.load_modules()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
