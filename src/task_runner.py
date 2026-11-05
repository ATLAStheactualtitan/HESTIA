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
        self.workers: dict[str, asyncio.Task] = {}

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
            if task.enabled:
                self._start_worker(task)

    async def stop(self):
        self.running = False
        workers = list(self.workers.values())
        self.workers.clear()
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def set_enabled(self, name: str, enabled: bool):
        task = self.tasks.get(name)
        if task is None:
            raise KeyError(f"Task '{name}' is not registered.")

        task.enabled = enabled
        worker = self.workers.pop(name, None)
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if enabled and self.running:
            self._start_worker(task)
        return task.enabled

    def _start_worker(self, task: ScheduledTask):
        worker = self.workers.get(task.name)
        if worker is None or worker.done():
            self.workers[task.name] = asyncio.create_task(self._run_task(task))

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
