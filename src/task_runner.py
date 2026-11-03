import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class ScheduledTask:
    name: str
    callback: Callable[[], Awaitable[None]]
    interval_seconds: float = 60.0
    enabled: bool = True
    last_run: float = field(default_factory=time.monotonic)


class TaskRunner:
    def __init__(self, bot):
        self.bot = bot
        self.tasks: dict[str, ScheduledTask] = {}
        self.running = False

    def add(self, name: str, callback: Callable[[], Awaitable[None]], interval_seconds: float = 60.0):
        if not callable(callback):
            raise TypeError("Task callback must be callable.")

        self.tasks[name] = ScheduledTask(
            name=name,
            callback=callback,
            interval_seconds=interval_seconds,
        )
        return self.tasks[name]

    async def start(self):
        if self.running:
            return

        self.running = True
        for task in self.tasks.values():
            asyncio.create_task(self._run_task(task))

    async def stop(self):
        self.running = False

    async def run_now(self, name: str):
        task = self.tasks.get(name)
        if not task:
            raise KeyError(f"Task '{name}' is not registered.")
        await task.callback()

    async def _run_task(self, task: ScheduledTask):
        while self.running and task.enabled:
            try:
                await task.callback()
            except Exception as exc:
                print(f"Task '{task.name}' failed: {exc}")

            task.last_run = time.monotonic()
            await asyncio.sleep(task.interval_seconds)
