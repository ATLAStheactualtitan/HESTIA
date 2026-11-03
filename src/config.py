import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
COMMAND_PREFIX = ""  # Slash commands only; no text command prefix is used.
API_BASE_URL = os.getenv("API_BASE_URL", "")
