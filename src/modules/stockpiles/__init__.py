"""HESTIA | Konsortium stockpile tracking: commands, settings, reminders, and war-rollover resets."""

from config import WARAPI_BASE_URL

from .config import set_war_number
from .feature import send_due_stockpile_reminders, setup_stockpile_commands
from .runtime import handle_stockpile_war_rollover
from .settings import settings_command
from .warapi_client import WarAPIClient


def setup(bot):
    setup_stockpile_commands(bot)
    bot.tree.add_command(settings_command, override=True)
    bot.service_status.setdefault("WarAPI", True)
    war_client = WarAPIClient(WARAPI_BASE_URL)

    async def remind():
        sent = await send_due_stockpile_reminders(bot)
        if sent:
            print(f"[Stockpile] Sent {sent} reminder(s)")

    async def track_war_number():
        if not bot.service_status.get("WarAPI", True):
            return
        war_number = str((await war_client.get_war()).get("warNumber") or "").strip()
        if war_number:
            set_war_number(war_number)
            handle_stockpile_war_rollover(war_number)

    bot.task_runner.add("stockpile-reminders", remind, interval_seconds=300)
    bot.task_runner.add("stockpile-war-number", track_war_number, interval_seconds=300)
