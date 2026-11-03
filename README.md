# Modular Discord Bot Framework

This project is a barebones modular Discord bot written in Python.

## Structure

- `src/main.py` — starts the bot
- `src/bot.py` — custom bot class and module loader
- `src/config.py` — token and prefix settings
- `src/modules/` — place individual command modules here

## How to run

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your Discord bot token.
3. Start the bot:
   ```bash
   python src/main.py
   ```

## Adding a module

Create a Python file inside `src/modules/` with a `setup(bot)` function.

Example:

```python
from discord.ext import commands


def setup(bot):
    @bot.command(name="hello")
    async def hello(ctx):
        await ctx.send("Hello from a module!")
```

The bot will automatically import and register modules placed in `src/modules/`.
