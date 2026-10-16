"""Tiny local Alertmanager webhook receiver for ClueStack demos.

Run:
    python scripts/alert_receiver.py

Then point Alertmanager at:
    http://host.docker.internal:9999/alerts

or, from a local process:
    http://localhost:9999/alerts
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class AlertHandler(BaseHTTPRequestHandler):
    """Print Alertmanager webhook payloads to stdout."""

    def do_POST(self) -> None:
        """Accept and display one Alertmanager webhook request."""

        length = int(self.headers.get("content-length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        try:
            parsed = json.loads(payload)
            payload = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pass
        print("\n=== ClueStack Alert Received ===")
        print(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, format: str, *args) -> None:
        """Keep default request logs concise during demos."""

        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    """Run the receiver on port 9999."""

    server = HTTPServer(("0.0.0.0", 9999), AlertHandler)
    print("ClueStack alert receiver listening on http://0.0.0.0:9999/alerts")
    server.serve_forever()


if __name__ == "__main__":
    main()
