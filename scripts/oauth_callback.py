#!/usr/bin/env python3
"""One-shot MYOB OAuth2 authorization helper.

Must be run ON THE SERVER (e.g. over SSH), not on your laptop: it listens on
127.0.0.1:8787, which is where nginx proxies the redirect to
https://myobot.example.com/callback (see README.md). It prints the MYOB
consent URL — open that in a browser on your own machine, approve, and your
browser's redirect will reach nginx -> this listener on the server.

It exchanges the returned authorization code for an access + refresh token
and saves them to tokens.json.

Run this once (or whenever the refresh token needs to be re-issued) — it
handles exactly one request and then exits. Everyday automation should read
tokens.json directly rather than going through this flow again.

Usage (on the server):
    MYOB_CLIENT_ID=xxx MYOB_CLIENT_SECRET=yyy python3 scripts/oauth_callback.py
"""

import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

REDIRECT_URI = "https://myobot.example.com/callback"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8787

AUTH_ENDPOINT = "https://secure.myob.com/oauth2/account/authorize"
TOKEN_ENDPOINT = "https://secure.myob.com/oauth2/v1/authorize/"
SCOPE = "CompanyFile"

TOKENS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tokens.json")

# NOTE: verify AUTH_ENDPOINT / TOKEN_ENDPOINT / SCOPE against the current MYOB
# developer documentation for your app before relying on this — confirm
# against https://developer.myob.com once you have portal access.


def build_auth_url(client_id: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()

    request = Request(
        TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"Token exchange failed ({e.code}): {body}")


def make_handler(expected_state: str, client_id: str, client_secret: str, result: dict):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(parsed.query)

            if "error" in query:
                self._respond(400, f"MYOB returned an error: {query['error'][0]}")
                result["error"] = query["error"][0]
                return

            state = query.get("state", [None])[0]
            code = query.get("code", [None])[0]

            if state != expected_state:
                self._respond(400, "State mismatch — possible CSRF, aborting.")
                result["error"] = "state_mismatch"
                return

            if not code:
                self._respond(400, "No authorization code in callback.")
                result["error"] = "missing_code"
                return

            tokens = exchange_code(client_id, client_secret, code)
            result["tokens"] = tokens
            self._respond(200, "Authorization complete. You can close this tab.")

        def _respond(self, status: int, message: str):
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode())

        def log_message(self, format, *args):
            pass  # keep stdout clean; nothing sensitive is logged either way

    return CallbackHandler


def main():
    client_id = os.environ.get("MYOB_CLIENT_ID")
    client_secret = os.environ.get("MYOB_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set MYOB_CLIENT_ID and MYOB_CLIENT_SECRET environment variables.")

    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, state)

    # Bind the listener before inviting anyone to click the URL, so it's
    # guaranteed ready to catch the redirect the moment MYOB sends it.
    result: dict = {}
    handler_cls = make_handler(state, client_id, client_secret, result)
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), handler_cls)

    print("This script listens on 127.0.0.1:8787, so it must run on the server")
    print("itself (e.g. over SSH) — nginx proxies the redirect there.")
    print()
    print("Open this URL in a browser on YOUR OWN machine and approve access.")
    print("Approval happens on MYOB's own consent screen; this script only")
    print("catches the redirect afterwards:")
    print(auth_url)
    print()
    print(f"Listening on {LISTEN_HOST}:{LISTEN_PORT} for the redirect...")
    server.handle_request()  # blocks for exactly one request, then returns

    if "error" in result:
        sys.exit(f"Authorization failed: {result['error']}")

    with open(TOKENS_FILE, "w") as f:
        json.dump(result["tokens"], f, indent=2)
    os.chmod(TOKENS_FILE, 0o600)

    print(f"Saved tokens to {TOKENS_FILE}")


if __name__ == "__main__":
    main()
