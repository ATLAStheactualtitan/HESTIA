import sys
import threading
import time

from dashboard import start_dashboard
from supervisor import BotSupervisor


def _watch_terminal_for_stop(supervisor):
    for line in sys.stdin:
        if line.strip().upper() == "STOP":
            try:
                print(f"Bot: {supervisor.stop_bot()}. Dashboard stays open; press Ctrl+C to exit.")
            except (RuntimeError, TimeoutError) as exc:
                print(f"Could not stop the bot: {exc}")


def main():
    supervisor = BotSupervisor()
    dashboard_server = start_dashboard(supervisor)
    threading.Thread(target=_watch_terminal_for_stop, args=(supervisor,), daemon=True).start()
    try:
        print("Starting HESTIA bot worker… (type STOP and press Enter to stop the bot)")
        supervisor.start_bot()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping HESTIA supervisor…")
    finally:
        supervisor.close()
        dashboard_server.shutdown()
        dashboard_server.server_close()


if __name__ == "__main__":
    main()
