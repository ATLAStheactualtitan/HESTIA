from typing import Optional

from .config import clear_all_stockpiles, get_config_value, set_config_value

STOCKPILE_LAST_RESET_WAR_CONFIG_KEY = "stockpile_last_reset_war"


def handle_stockpile_war_rollover(current_war_number: str) -> Optional[int]:
    current = str(current_war_number or "").strip()
    if not current or current == "Unknown":
        return None

    previous = get_config_value(STOCKPILE_LAST_RESET_WAR_CONFIG_KEY)
    if previous in (None, ""):
        set_config_value(STOCKPILE_LAST_RESET_WAR_CONFIG_KEY, current)
        return 0
    if str(previous) == current:
        return None

    cleared_guilds = clear_all_stockpiles()
    if not set_config_value(STOCKPILE_LAST_RESET_WAR_CONFIG_KEY, current):
        print(f"[Stockpile] Cleared records but failed to save rollover marker for war {current}.")
    print(
        "[Stockpiles] War rollover detected by war number change | "
        f"previous={previous} current={current} cleared_guilds={cleared_guilds}"
    )
    return cleared_guilds
