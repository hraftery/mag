#!/usr/bin/env python3
"""Persistent thin proxy: validates a mag bearer token's scope
(token_store.authorize), then forwards the request to the real MYOB API
using the single MYOB OAuth grant in tokens.json (myob_client.raw_request),
and relays MYOB's response back to the caller unmodified - no reshaping, so
MYOB's own docs stay the source of truth for clients, and a MYOB schema
change never requires a change here.

See README: "Client access: token issuer + thin proxy".

Binds to 127.0.0.1 only. nginx should be the sole internet-facing front
door, proxying e.g. /proxy/ to this port - the same pattern oauth_callback.py
already uses for /callback.

Unlike oauth_callback.py, this is a long-running service (many clients, many
requests over time), not a one-shot listener.

Usage (on the server):
    MYOB_CLIENT_ID=xxx MYOB_CLIENT_SECRET=yyy proxy.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from mag.lib import myob_client, token_store
from mag.lib.paths import DATA_DIR

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("MAG_PROXY_PORT", "8788"))
PATH_PREFIX = "/proxy/"

AUDIT_LOG = os.path.join(DATA_DIR, "proxy_audit.log")


def audit(token_name: str, method: str, path: str, status: int) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {token_name} {method} {path} -> {status}\n"
    try:
        # dirname(AUDIT_LOG), not DATA_DIR, so tests can redirect AUDIT_LOG.
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(line)
    except OSError as e:
        # A broken log must never take down request serving.
        print(f"WARNING: could not write audit log: {e}", file=sys.stderr)


class ProxyHandler(BaseHTTPRequestHandler):
    def _handle(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith(PATH_PREFIX):
            self._respond(404, b'{"error": "not found"}', "application/json")
            return
        myob_path = "/" + parsed.path[len(PATH_PREFIX):]

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._respond(401, b'{"error": "missing bearer token"}', "application/json")
            audit("-", self.command, myob_path, 401)
            return
        raw_token = auth[len("Bearer "):]

        record = token_store.authorize(raw_token, self.command, myob_path)
        if not record:
            self._respond(403, b'{"error": "forbidden"}', "application/json")
            audit("invalid-or-unscoped", self.command, myob_path, 403)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        content_type = self.headers.get("Content-Type")
        # $filter/$orderby/$top/$skip are always single-valued in MYOB's API,
        # so a plain dict (rather than preserving repeated keys) is fine here.
        query = dict(parse_qsl(parsed.query)) if parsed.query else None

        try:
            status, resp_ctype, data = myob_client.raw_request(
                self.command, myob_path, params=query, body=body, content_type=content_type
            )
        except myob_client.UpstreamUnreachable as e:
            self._respond(502, json.dumps({"error": f"MYOB unreachable: {e}"}).encode(), "application/json")
            audit(record["name"], self.command, myob_path, 502)
            return

        self._respond(status, data, resp_ctype or "application/json")
        audit(record["name"], self.command, myob_path, status)

    def _respond(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def log_message(self, format, *args):
        pass  # audit() above is the real log; keep stdout clean


def main():
    if not os.environ.get("MYOB_CLIENT_ID") or not os.environ.get("MYOB_CLIENT_SECRET"):
        sys.exit("Set MYOB_CLIENT_ID and MYOB_CLIENT_SECRET environment variables.")

    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(f"mag proxy listening on {LISTEN_HOST}:{LISTEN_PORT}{PATH_PREFIX}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
