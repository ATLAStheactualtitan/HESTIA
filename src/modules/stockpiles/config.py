import json
import os
from typing import Any, Optional

CONFIG_FILE = os.getenv(
    "STOCKPILE_CONFIG_FILE",
    os.path.join(os.path.dirname(__file__), "stockpile_config.json"),
)


def load_config() -> dict[str, Any]:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
                return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[StockpileConfig] Could not load config: {exc}")
    return {}


def save_config(config: dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(CONFIG_FILE)), exist_ok=True)
        temporary_path = f"{CONFIG_FILE}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        os.replace(temporary_path, CONFIG_FILE)
        return True
    except OSError as exc:
        print(f"[StockpileConfig] Could not save config: {exc}")
        return False


def get_config_value(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set_config_value(key: str, value: Any) -> bool:
    config = load_config()
    config[key] = value
    return save_config(config)


def get_war_number() -> Optional[str]:
    value = get_config_value("war_number")
    return str(value) if value is not None else None


def set_war_number(war_number: str) -> bool:
    return set_config_value("war_number", str(war_number))


def get_guild_config(guild_id: int, key: str, default: Any = None) -> Any:
    guilds = load_config().get("guilds", {})
    if not isinstance(guilds, dict):
        return default
    guild_config = guilds.get(str(guild_id), {})
    if not isinstance(guild_config, dict):
        return default
    return guild_config.get(key, default)


def set_guild_config(guild_id: int, key: str, value: Any) -> bool:
    config = load_config()
    guilds = config.setdefault("guilds", {})
    if not isinstance(guilds, dict):
        return False
    guild_config = guilds.setdefault(str(guild_id), {})
    if not isinstance(guild_config, dict):
        return False
    guild_config[key] = value
    return save_config(config)


def delete_guild_config(guild_id: int, key: str) -> bool:
    config = load_config()
    guilds = config.get("guilds", {})
    if not isinstance(guilds, dict):
        return False
    guild_config = guilds.get(str(guild_id))
    if not isinstance(guild_config, dict) or key not in guild_config:
        return False
    del guild_config[key]
    return save_config(config)


def clear_all_stockpiles() -> int:
    config = load_config()
    guilds = config.get("guilds", {})
    if not isinstance(guilds, dict):
        return 0
    cleared = 0
    for guild_config in guilds.values():
        if isinstance(guild_config, dict) and "stockpiles" in guild_config:
            del guild_config["stockpiles"]
            cleared += 1
    if cleared:
        save_config(config)
    return cleared
