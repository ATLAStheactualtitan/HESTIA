"""Child-process entry point that runs the Discord bot and receives controls."""

import asyncio
import queue
import time

from bot import ModularBot
from config import DISCORD_TOKEN


def _status_snapshot(bot):
    ready = bot.is_ready()
    task_runner = bot.task_runner
    module_status = bot.module_status or {
        name: "active" for name in bot.loaded_modules
    }
    return {
        "bot": {
            "ready": ready,
            "paused": bot.operations_paused,
            "stopping": bot.is_closed(),
            "process_running": True,
            "name": str(bot.user) if bot.user else "HESTIA",
            "latency_ms": round(bot.latency * 1000) if ready else None,
            "uptime_seconds": max(0, int(time.monotonic() - bot.started_at)),
            "server_count": len(bot.guilds),
        },
        "api_configured": bool(bot.api_client.base_url),
        "modules": [
            {"name": name, "status": status}
            for name, status in sorted(module_status.items())
        ],
        "services": [
            {"name": name, "status": "active" if enabled else "disabled"}
            for name, enabled in sorted(bot.service_status.items())
        ],
        "tasks": [
            {
                "name": name,
                "status": (
                    "active"
                    if task_runner.running and task.enabled
                    else "paused"
                    if not task.enabled
                    else "stopped"
                ),
            }
            for name, task in sorted(task_runner.tasks.items())
        ],
        "scheduler_running": task_runner.running,
    }


async def _handle_control(bot, payload):
    action = payload.get("action")
    if action == "shutdown":
        await bot.close()
        return "stopping"
    if action == "pause":
        return await bot.set_operations_paused(True)
    if action == "resume":
        return await bot.set_operations_paused(False)

    enabled = payload.get("enabled")
    if type(enabled) is not bool:
        raise ValueError("The 'enabled' field must be a boolean.")
    name = str(payload.get("name", ""))
    if action == "module":
        return await bot.set_module_enabled(name, enabled)
    if action == "service":
        return await bot.set_service_enabled(name, enabled)
    if action == "task":
        await bot.task_runner.set_enabled(name, enabled)
        return "active" if enabled else "disabled"
    raise ValueError("Unknown control action.")


async def _run_control_channel(bot, command_queue, response_queue):
    async def report_status():
        while not bot.is_closed():
            try:
                response_queue.put({"type": "status", "snapshot": _status_snapshot(bot)})
            except (BrokenPipeError, EOFError, OSError):
                await bot.close()
                return
            await asyncio.sleep(1)

    async def receive_controls():
        while not bot.is_closed():
            try:
                request = await asyncio.to_thread(command_queue.get, True, 0.25)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                await bot.close()
                return

            request_id = request.get("id", "")
            payload = request.get("payload", {})
            try:
                result = await _handle_control(bot, payload)
                response_queue.put(
                    {"type": "reply", "id": request_id, "result": {"status": result}}
                )
                if payload.get("action") == "shutdown":
                    return
            except Exception as exc:
                response_queue.put(
                    {
                        "type": "reply",
                        "id": request_id,
                        "result": {"error": str(exc)[:300]},
                    }
                )

    asyncio.create_task(report_status(), name="dashboard-status-reporter")
    asyncio.create_task(receive_controls(), name="dashboard-control-receiver")


def run_bot_worker(command_queue, response_queue):
    if not DISCORD_TOKEN:
        snapshot = {
            "bot": {
                "ready": False,
                "paused": False,
                "stopping": False,
                "process_running": True,
                "name": "HESTIA",
                "latency_ms": None,
                "uptime_seconds": 0,
                "server_count": 0,
                "error": "DISCORD_TOKEN is missing. Configure it in the root .env file.",
            },
            "api_configured": False,
            "modules": [],
            "services": [],
            "tasks": [],
            "scheduler_running": False,
        }
        response_queue.put(
            {"type": "status", "snapshot": snapshot}
        )
        raise RuntimeError("DISCORD_TOKEN is missing. Configure it in the root .env file.")

    bot = ModularBot(command_prefix="")
    bot.load_modules()

    async def setup_hook():
        await _run_control_channel(bot, command_queue, response_queue)

    bot.setup_hook = setup_hook
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as exc:
        try:
            snapshot = _status_snapshot(bot)
            snapshot["bot"]["error"] = str(exc)[:300]
            response_queue.put(
                {"type": "status", "snapshot": snapshot}
            )
        except Exception:
            pass
        raise
