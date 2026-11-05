import asyncio
import importlib
import pkgutil
import time
from pathlib import Path

import discord
from discord.ext import commands

from api_client import APIClient
from config import API_BASE_URL
from task_runner import TaskRunner


class ControlledCommandTree(discord.app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if getattr(self.client, "operations_paused", False):
            await interaction.response.send_message(
                "HESTIA is paused from its local control panel.", ephemeral=True
            )
            return False
        return await super().interaction_check(interaction)


class ModularBot(commands.Bot):
    def __init__(self, command_prefix: str = "", module_dir: str | Path | None = None, **kwargs):
        intents = kwargs.pop("intents", discord.Intents.default())
        intents.message_content = False

        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            tree_cls=ControlledCommandTree,
            **kwargs,
        )

        self.module_dir = Path(module_dir) if module_dir else Path(__file__).resolve().parent / "modules"
        self.loaded_modules = []
        self.module_status: dict[str, str] = {}
        self.module_commands: dict[str, list[discord.app_commands.Command]] = {}
        self.module_tasks: dict[str, dict[str, bool]] = {}
        self.service_status: dict[str, bool] = {}
        self.operations_paused = False
        self.dashboard_loop: asyncio.AbstractEventLoop | None = None
        self._runner_was_running = False
        self.started_at = time.monotonic()
        self.api_client = APIClient(base_url=API_BASE_URL)
        self.task_runner = TaskRunner(self)

    def load_module(self, module_name: str):
        self.module_status[module_name] = "loading"
        existing_commands = {command.name for command in self.tree.get_commands()}
        existing_tasks = set(self.task_runner.tasks)
        try:
            module = importlib.import_module(f"modules.{module_name}")
            if not hasattr(module, "setup"):
                raise AttributeError(
                    f"Module '{module_name}' does not define a setup(bot) function."
                )
            module.setup(self)
            self.module_commands[module_name] = [
                command
                for command in self.tree.get_commands()
                if command.name not in existing_commands
            ]
            self.module_tasks[module_name] = {
                name: task.enabled
                for name, task in self.task_runner.tasks.items()
                if name not in existing_tasks
            }
            self.loaded_modules.append(module_name)
            self.module_status[module_name] = "active"
            return module
        except Exception:
            for command in self.tree.get_commands():
                if command.name not in existing_commands:
                    self.tree.remove_command(command.name)
            for name in set(self.task_runner.tasks) - existing_tasks:
                self.task_runner.tasks.pop(name, None)
            self.module_status[module_name] = "error"
            raise

    async def set_module_enabled(self, module_name: str, enabled: bool):
        if module_name not in self.module_status:
            raise KeyError(f"Unknown module: {module_name}")
        if enabled and self.module_status[module_name] == "error":
            raise RuntimeError("A module that failed during startup cannot be enabled without restarting the bot.")

        module_commands = self.module_commands.get(module_name, [])
        module_tasks = self.module_tasks.get(module_name, {})
        if not module_commands and not module_tasks:
            raise RuntimeError(f"Module '{module_name}' has no controllable commands or tasks.")

        for command in module_commands:
            current = self.tree.get_command(command.name)
            if enabled and current is None:
                self.tree.add_command(command)
            elif not enabled and current is not None:
                self.tree.remove_command(command.name)

        for task_name, originally_enabled in module_tasks.items():
            task_enabled = enabled and originally_enabled
            if task_name in self.task_runner.tasks:
                await self.task_runner.set_enabled(task_name, task_enabled)

        self.module_status[module_name] = "active" if enabled else "disabled"
        if self.is_ready():
            await self.tree.sync()
        return self.module_status[module_name]

    async def set_service_enabled(self, service_name: str, enabled: bool):
        if service_name not in self.service_status:
            raise KeyError(f"Unknown service: {service_name}")
        self.service_status[service_name] = enabled
        return "active" if enabled else "disabled"

    async def set_operations_paused(self, paused: bool):
        if paused == self.operations_paused:
            return "paused" if paused else "active"

        if paused:
            self._runner_was_running = self.task_runner.running
            self.operations_paused = True
            if self.task_runner.running:
                await self.task_runner.stop()
        else:
            self.operations_paused = False
            if self._runner_was_running and self.task_runner.tasks:
                await self.task_runner.start()
            self._runner_was_running = False
        return "paused" if paused else "active"

    def load_modules(self):
        if not self.module_dir.exists():
            return []

        for _, module_name, _ in pkgutil.iter_modules([str(self.module_dir)]):
            if module_name.startswith("_"):
                continue
            try:
                self.load_module(module_name)
            except Exception as exc:
                print(f"Could not load module '{module_name}': {exc}")

        return self.loaded_modules

    async def on_ready(self):
        self.dashboard_loop = asyncio.get_running_loop()
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Loaded modules: {', '.join(self.loaded_modules) if self.loaded_modules else 'none'}")

        if hasattr(self, "tree"):
            try:
                await self.tree.sync()
            except Exception as exc:
                print(f"Could not sync slash commands: {exc}")

        if self.task_runner.tasks and not self.operations_paused:
            await self.task_runner.start()

    async def close(self):
        if hasattr(self, "task_runner"):
            await self.task_runner.stop()
        await super().close()
