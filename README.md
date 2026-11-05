# Modular Discord Bot Framework

This project is a barebones modular Discord bot written in Python.

## Structure

- `src/main.py` — starts the bot
- `src/bot.py` — custom bot class and module loader
- `src/config.py` — token and prefix settings
- `src/modules/` — place individual command modules here
- `src/modules/stockpiles/` — stockpile tracking (`/stockpile-add|edit|change|list|remove`), `/settings`, 36h expiry reminders, Excel logs, and per-war resets

Stockpile access is role based: `/stockpile-list` only shows stockpiles whose access role the caller has. Admins choose the default role (and everything else) with `/settings`; until then it is `@everyone`, or `STOCKPILE_VERIFIED_ROLE_ID` from `.env` if set. Data is stored in `src/modules/stockpiles/` (Git-ignored).

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
   A local dashboard window opens in your browser at `http://127.0.0.1:8765`.

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

## Foxhole war number

The stockpile module polls the official WarAPI for the war number and clears stockpiles when a new war starts. Configure `WARAPI_SHARD` in `.env` (`1`, `2`, `3` or `dev`), or point `WARAPI_BASE_URL` at another API root.

## Desktop dashboard

Starting the bot also opens a local browser dashboard showing bot connectivity, uptime, latency, server count, module states, and background tasks. The dashboard refreshes automatically and is available only on the local computer at `http://127.0.0.1:8765`.

The dashboard runs in a supervisor process separate from the Discord bot worker. It provides controls to start/stop the worker, pause/resume bot functions, enable or disable loaded modules, pause individual scheduled tasks, and disable API services. Stopping the worker leaves the dashboard open so you can start it again there. Pausing functions blocks slash commands and stops scheduled jobs; resuming restores previously running jobs. Press Ctrl+C in the launcher terminal to close both the dashboard supervisor and the bot. Controls are bound to loopback and require a per-run authorization token.
