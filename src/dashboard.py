"""Local browser dashboard for the HESTIA bot."""

import hmac
import ipaddress
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit


DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765


def start_dashboard(controller, host=DASHBOARD_HOST, port=DASHBOARD_PORT, open_window=True):
    """Serve the dashboard locally and optionally open it in a browser window."""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("The control panel must bind only to a loopback address.")
    except ValueError as exc:
        raise ValueError("The control panel must bind only to 127.0.0.1 or ::1.") from exc
    control_token = secrets.token_urlsafe(32)

    def valid_host_header(request_handler):
        host_label = f"[{host}]" if ":" in host else host
        expected_host = f"{host_label}:{server.server_address[1]}"
        return request_handler.headers.get("Host", "").lower() == expected_host.lower()

    def require_boolean(payload):
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            raise ValueError("The 'enabled' field must be a boolean.")
        return enabled

    def apply_control(payload):
        action = payload.get("action")
        if action not in {"start", "shutdown", "pause", "resume", "module", "service", "task"}:
            raise ValueError("Unknown control action.")
        if action in {"module", "service", "task"}:
            require_boolean(payload)
        result = controller.apply_control(payload)
        if isinstance(result, dict):
            return result
        return {"status": result}

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not valid_host_header(self):
                self.send_error(421)
                return
            route = urlsplit(self.path).path
            if route == "/api/status":
                body = json.dumps(controller.status_snapshot()).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif route in {"/", "/index.html"}:
                page = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
                body = page.replace("__HESTIA_CONTROL_TOKEN__", control_token).encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not valid_host_header(self):
                self.send_error(421)
                return
            if urlsplit(self.path).path != "/api/control":
                self.send_error(404)
                return
            supplied_token = self.headers.get("X-Hestia-Control-Token", "")
            if not hmac.compare_digest(supplied_token, control_token):
                self._send_json(403, {"error": "Control authorization failed."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 4096:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object.")
                result = apply_control(payload)
                self._send_json(200, result)
            except Exception as exc:
                self._send_json(400, {"error": str(exc)[:300]})

        def _send_json(self, status_code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="hestia-dashboard", daemon=True).start()

    actual_port = server.server_address[1]
    host_label = f"[{host}]" if ":" in host else host
    url = f"http://{host_label}:{actual_port}"
    print(f"HESTIA dashboard: {url}")
    if open_window:
        try:
            if not webbrowser.open(url, new=1):
                print("Could not open a browser window automatically; use the URL above.")
        except Exception as exc:
            print(f"Could not open a browser window: {exc}; use the URL above.")
    return server
