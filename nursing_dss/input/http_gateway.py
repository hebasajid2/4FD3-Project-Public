"""Optional network entry point for the INPUT stage.

Real call-bell hardware (or any script) can POST a signal:

    curl -X POST localhost:8765/bell -d '{"bell_id": 1011}'

The gateway only parses the signal and hands it to the routing system.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .signals import InvalidSignal, parse_signal


def make_server(routing_system, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/bell":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                signal = parse_signal(payload, source="HTTP", received_at=routing_system.clock())
            except (json.JSONDecodeError, InvalidSignal) as exc:
                return self._send(400, {"error": str(exc)})
            result = routing_system.handle_signal(signal)
            self._send(200 if result.ok else 202, result.to_dict())

        def log_message(self, *args):  # keep test/CLI output quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_background(routing_system, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = make_server(routing_system, host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
