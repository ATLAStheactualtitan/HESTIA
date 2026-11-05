"""Keep the local dashboard alive while the Discord bot runs in a child process."""

import multiprocessing
import queue
import threading
import uuid

from bot_worker import run_bot_worker


class BotSupervisor:
    def __init__(self):
        self._context = multiprocessing.get_context("spawn")
        self._commands = self._context.Queue()
        self._responses = self._context.Queue()
        self._lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._process = None
        self._closed = threading.Event()
        self._state = self._offline_state()
        self._response_thread = threading.Thread(
            target=self._read_responses,
            name="hestia-supervisor-responses",
            daemon=True,
        )
        self._watch_thread = threading.Thread(
            target=self._watch_process,
            name="hestia-supervisor-watch",
            daemon=True,
        )
        self._response_thread.start()
        self._watch_thread.start()

    @staticmethod
    def _offline_state():
        return {
            "bot": {
                "ready": False,
                "paused": False,
                "stopping": False,
                "process_running": False,
                "name": "HESTIA",
                "latency_ms": None,
                "uptime_seconds": 0,
                "server_count": 0,
            },
            "api_configured": False,
            "modules": [],
            "services": [],
            "tasks": [],
            "scheduler_running": False,
        }

    def start_bot(self):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("The control panel supervisor is shutting down.")
            if self._process is not None and self._process.is_alive():
                return "already running"
            if self._process is not None:
                self._process.join(timeout=0)

            self._drain_queue(self._commands)
            self._drain_queue(self._responses)
            self._process = self._context.Process(
                target=run_bot_worker,
                args=(self._commands, self._responses),
                name="hestia-discord-bot",
                daemon=False,
            )
            self._process.start()
            self._state = self._offline_state()
            self._state["bot"]["process_running"] = True
            return "starting"

    @staticmethod
    def _drain_queue(target_queue):
        try:
            while True:
                target_queue.get_nowait()
        except queue.Empty:
            pass

    def stop_bot(self):
        with self._lock:
            if self._process is None or not self._process.is_alive():
                return "already stopped"
        return self._request({"action": "shutdown"}, timeout=15)

    def status_snapshot(self):
        with self._lock:
            state = {**self._state, "bot": dict(self._state["bot"])}
            state["bot"]["process_running"] = bool(
                self._process is not None and self._process.is_alive()
            )
            if not state["bot"]["process_running"]:
                state["bot"]["ready"] = False
                state["bot"]["stopping"] = False
            return state

    def apply_control(self, payload):
        action = payload.get("action")
        if action == "start":
            return {"status": self.start_bot()}
        if action == "shutdown":
            return {"status": self.stop_bot()}
        if action not in {"pause", "resume", "module", "service", "task"}:
            raise ValueError("Unknown control action.")
        with self._lock:
            if self._process is None or not self._process.is_alive():
                raise RuntimeError("The bot process is stopped. Start it before changing controls.")
        return self._request(payload)

    def _request(self, payload, timeout=20):
        request_id = uuid.uuid4().hex
        event = threading.Event()
        result: dict = {}
        with self._pending_lock:
            self._pending[request_id] = (event, result)
        self._commands.put({"id": request_id, "payload": payload})
        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError("The bot process did not respond to the control action.")
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def _read_responses(self):
        while not self._closed.is_set():
            try:
                message = self._responses.get(timeout=0.25)
            except queue.Empty:
                continue
            if message.get("type") == "status":
                with self._lock:
                    self._state = message["snapshot"]
                    self._state["bot"]["process_running"] = True
            elif message.get("type") == "reply":
                with self._pending_lock:
                    pending = self._pending.pop(message.get("id"), None)
                if pending:
                    event, result = pending
                    result.update(message.get("result", {}))
                    event.set()

    def _watch_process(self):
        while not self._closed.wait(0.5):
            with self._lock:
                process = self._process
                if process is not None and not process.is_alive():
                    process.join(timeout=0)
                    self._state["bot"]["ready"] = False
                    self._state["bot"]["stopping"] = False
                    self._state["bot"]["process_running"] = False

    def close(self):
        try:
            self.stop_bot()
        except (RuntimeError, TimeoutError):
            with self._lock:
                process = self._process
                if process is not None and process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        self._closed.set()
        self._response_thread.join(timeout=1)
        self._watch_thread.join(timeout=1)
        for target_queue in (self._commands, self._responses):
            target_queue.close()
            target_queue.join_thread()
