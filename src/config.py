import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
COMMAND_PREFIX = ""  # Slash commands only; no text command prefix is used.
API_BASE_URL = os.getenv("API_BASE_URL", "")

WARAPI_SHARD = os.getenv("WARAPI_SHARD", "1").strip().lower()
WARAPI_ENDPOINTS = {
	"1": "https://war-service-live.foxholeservices.com/api",
	"2": "https://war-service-live-2.foxholeservices.com/api",
	"3": "https://war-service-live-3.foxholeservices.com/api",
	"dev": "https://war-service-dev.foxholeservices.com/api",
}
WARAPI_BASE_URL = os.getenv(
	"WARAPI_BASE_URL", WARAPI_ENDPOINTS.get(WARAPI_SHARD, WARAPI_ENDPOINTS["1"])
).rstrip("/")
