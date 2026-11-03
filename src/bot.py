import asyncio
import importlib
import pkgutil
from pathlib import Path

import discord
from discord.ext import commands

from api_client import APIClient
from config import API_BASE_URL
from task_runner import TaskRunner


class ModularBot(commands.Bot):
    def __init__(self, command_prefix: str = "", module_dir: str | Path | None = None, **kwargs):
        intents = kwargs.pop("intents", discord.Intents.default())
        intents.message_content = False

        super().__init__(command_prefix=command_prefix, intents=intents, **kwargs)

        self.module_dir = Path(module_dir) if module_dir else Path(__file__).resolve().parent / "modules"
        self.loaded_modules = []
        self.api_client = APIClient(base_url=API_BASE_URL)
        self.task_runner = TaskRunner(self)

    def load_module(self, module_name: str):
        module = importlib.import_module(f"modules.{module_name}")

        if hasattr(module, "setup"):
            module.setup(self)
            self.loaded_modules.append(module_name)
            return module

        raise AttributeError(f"Module '{module_name}' does not define a setup(bot) function.")

    def load_modules(self):
        if not self.module_dir.exists():
            return []

        for _, module_name, _ in pkgutil.iter_modules([str(self.module_dir)]):
            if module_name.startswith("_"):
                continue
            self.load_module(module_name)

        return self.loaded_modules

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Loaded modules: {', '.join(self.loaded_modules) if self.loaded_modules else 'none'}")

        if hasattr(self, "tree"):
            try:
                await self.tree.sync()
            except Exception as exc:
                print(f"Could not sync slash commands: {exc}")

        if self.task_runner.tasks:
            await self.task_runner.start()
