"""One-shot MYOB OAuth2 authorization helper. Must be run ON THE SERVER.

This helper starts a listener on 127.0.0.1:8787, which is where nginx
proxies the redirect. It then prints the MYOB consent URL, which must be
opened LOCALLY in a browser. Once consent is granted, the redirect will
hit nginx via: https://<MAG_DOMAIN>/callback

It then exchanges the returned authorization code for an access + refresh
token, and saves them to tokens.json.

Run this once (or whenever the refresh token needs to be re-issued) — it
handles exactly one request and then exits. Everyday automation should read
tokens.json directly rather than going through this flow again.

MYOB's OAuth2.0 Authentication Guide:
https://apisupport.myob.com/hc/en-us/articles/13065472856719

Not runnable on its own - invoked via mag's "oauth" command:
    MYOB_CLIENT_ID=xxx MYOB_CLIENT_SECRET=yyy MAG_DOMAIN=zzz mag oauth
"""

import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from mag.lib import myob_client

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8787

AUTH_ENDPOINT = "https://secure.myob.com/oauth2/account/authorize"
TOKEN_ENDPOINT = "https://secure.myob.com/oauth2/v1/authorize"
# Apps must request the granular "sme-*" scopes:
# https://developer.myob.com/api/myob-business-api/api-overview/scopes/
ALL_SCOPES = [
    "sme-general-ledger",
    "sme-sales",                # eg. Invoices
    "sme-timebilling",
    "sme-inventory",
    "sme-contacts-customer",    # eg. Customers
    "sme-contacts-supplier",    # eg. Suppliers
    "sme-contacts-personal",
    "sme-contacts-employee",
    "sme-banking",              # eg. Transactions
    "sme-purchases",
    "sme-payroll",
    "sme-company-settings",     # eg. Company
    "sme-company-file"          # eg. Info
]
# Since a token only carries the scope it was originally consented for, we request
# *all* scopes once, and use our own token/scope scheme to control access for clients.
SCOPE = " ".join(ALL_SCOPES)


def build_auth_url(client_id: str, state: str, redirect_uri: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        # MYOB now requires the businessId (company file GUID) included in
        # the callback. "prompt=consent" ensures it is returned.
        "prompt": "consent",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"

def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = Request(TOKEN_ENDPOINT, method="POST", headers=headers, data=data)
    try:
        with urlopen(request, timeout=6) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"Token exchange failed ({e.code}): {body}")
    except URLError as e:
        raise SystemExit(f"Token exchange failed: could not reach {TOKEN_ENDPOINT} ({e.reason})")

def make_handler(expected_state: str, client_id: str, client_secret: str, redirect_uri: str, result: dict):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                # This consumes the one-shot handle_request() below, so the
                # real MYOB redirect (if it arrives after some stray request -
                # a browser prefetch, a health check) has no listener left to
                # catch it. Record it as an error rather than leaving result
                # empty, so main() below exits with a clear message instead
                # of crashing on a missing "tokens" key.
                result["error"] = f"unexpected request to {parsed.path!r}, not /callback"
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
            
            print("Callback received. Exchanging code for tokens...")
            tokens = exchange_code(client_id, client_secret, code, redirect_uri)
            # Capture businessId/businessName from the redirect. See prompt=consent.
            tokens["businessId"] = query.get("businessId", [None])[0]
            tokens["businessName"] = query.get("businessName", [None])[0]
            result["tokens"] = tokens
            self._respond(200, "Authorisation complete. You can close this tab.")
        
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
    domain = os.environ.get("MAG_DOMAIN")
    if not client_id or not client_secret:
        sys.exit("MYOB_CLIENT_ID and MYOB_CLIENT_SECRET environment variables must be set.")
    if not domain:
        sys.exit("MAG_DOMAIN environment variable must be set (the domain MYOB redirects back to).")
    redirect_uri = f"https://{domain}/callback"

    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, state, redirect_uri)

    # Bind the listener before inviting anyone to click the URL, so it's
    # ready to catch the redirect the moment MYOB sends it.
    result: dict = {}
    handler_cls = make_handler(state, client_id, client_secret, redirect_uri, result)
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), handler_cls)
    
    print(f"This script listens on {LISTEN_HOST}:{LISTEN_PORT}, so it must run on the same")
    print("server where nginx proxies the redirect there.")
    print()
    print("Open this URL in a browser locally and approve access:")
    print()
    print(auth_url)
    print()
    print("Approval happens on MYOB's own consent screen - this script only")
    print("catches the redirect afterwards.")
    print()
    print(f"Listening on {LISTEN_HOST}:{LISTEN_PORT} for the redirect...")
    server.handle_request()  # blocks for exactly one request, then returns
    
    if "error" in result:
        sys.exit(f"Authorization failed: {result['error']}")
    
    print("Tokens received. Saving...")
    myob_client.save_myob_tokens(result["tokens"])
    
    print("")
    print(f"Saved tokens to {myob_client.MYOB_TOKENS_FILE}")
    print("OAuth procedure successful. mag is now authorised to access MYOB.")

if __name__ == "__main__":
    sys.exit("Run this via: mag oauth (see setup.sh)")
