#!/usr/bin/env python3
"""Persistent thin proxy: validates a mag bearer token's scope
(token_store.authorise), then forwards the request to the real MYOB API
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

# Marks a response mag generated itself (never reached MYOB, or MYOB was
# unreachable) so a client can tell it apart from an error MYOB itself
# returned that mag is relaying unmodified below - which never carries this
# header, since mag never modifies those responses.
MAG_ERROR_HEADER = "X-Mag-Error"

# Hop-by-hop headers (RFC 7230 §6.1) describe *this* connection, not MYOB's
# response - meaningless (or wrong) to copy through to a different one.
# Content-Length is excluded too: it's recomputed from the actual outgoing
# body below, not copied from MYOB's.
_EXCLUDED_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
})


def _filter_relay_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """MYOB's response headers, minus the hop-by-hop/framing ones above -
    everything else (rate-limit headers, ETag, whatever else MYOB sends)
    passes through untouched, since it's not this proxy's job to guess
    which of MYOB's headers might matter to a given client."""
    relayed = [(name, value) for name, value in headers if name.lower() not in _EXCLUDED_RESPONSE_HEADERS]
    if not any(name.lower() == "content-type" for name, _ in relayed):
        relayed.append(("Content-Type", "application/json"))
    return relayed


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
            self._respond_error(404, "not found")
            return
        myob_path = "/" + parsed.path[len(PATH_PREFIX):]
        
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._respond_error(401, "missing bearer token")
            audit("-", self.command, myob_path, 401)
            return
        raw_token = auth[len("Bearer "):]
        
        record = token_store.authorise(raw_token, self.command, myob_path)
        if not record:
            self._respond_error(403, "forbidden")
            audit("invalid-or-unscoped", self.command, myob_path, 403)
            return
        
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        content_type = self.headers.get("Content-Type")
        # $filter/$orderby/$top/$skip are always single-valued in MYOB's API,
        # so a plain dict (rather than preserving repeated keys) is fine here.
        query = dict(parse_qsl(parsed.query)) if parsed.query else None
        
        try:
            status, resp_headers, data = myob_client.raw_request(
                self.command, myob_path, params=query, body=body, content_type=content_type
            )
        except myob_client.UpstreamUnreachable as e:
            self._respond_error(502, f"MYOB unreachable: {e}")
            audit(record["name"], self.command, myob_path, 502)
            return
        
        self._respond(status, data, _filter_relay_headers(resp_headers))
        audit(record["name"], self.command, myob_path, status)
    
    def _respond(self, status: int, body: bytes, headers: list[tuple[str, str]], mag_error: bool = False):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        if mag_error:
            self.send_header(MAG_ERROR_HEADER, "true")
        self.end_headers()
        self.wfile.write(body)
    
    def _respond_error(self, status: int, message: str):
        """Shorthand for the mag-generated {"error": ...} responses above -
        tagged with MAG_ERROR_HEADER, unlike the MYOB relay in _handle()."""
        self._respond(status, json.dumps({"error": message}).encode(), [("Content-Type", "application/json")], mag_error=True)
    
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
