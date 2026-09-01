"""Shared helpers for calling the MYOB Business (AccountRight) API using
tokens.json produced by oauth_callback.py.

Handles loading/saving tokens.json and refreshing the access token when it
expires. Access tokens last ~20 minutes and refresh tokens ~1 week, rotating
on every use. A refreshed pair is re-saved to tokens.json immediately.

Ref:
https://developer.myob.com/api/myob-business-api/api-overview/authentication/
https://apisupport.myob.com/hc/en-us/articles/360000513855-Building-an-AccountRight-API-request
https://apisupport.myob.com/hc/en-us/articles/360000477416-Refreshing-access-tokens-using-the-refresh-tokens

Some company files (typically ones migrated from desktop AccountRight) have
their own sign-in credentials separate from your MYOB account, and require an
x-myobapi-cftoken header alongside the OAuth token. If the client enables SSO
this means they only need to log into the AccountRight file using their my.myob
email and password and the username and password is now linked and NOT required.

Set MYOB_CF_USERNAME / MYOB_CF_PASSWORD to send it; if a call fails with 401
even right after a fresh token, that credentials-on-the-company-file case is
the most likely reason.
"""

import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS_FILE = os.path.join(ROOT_DIR, "tokens.json")

TOKEN_ENDPOINT = "https://secure.myob.com/oauth2/v1/authorize"
API_BASE = "https://api.myob.com/accountright"
API_VERSION = "v2"
TIMEOUT = 6


def load_myob_tokens() -> dict:
    if not os.path.exists(TOKENS_FILE):
        raise SystemExit(f"{TOKENS_FILE} not found — run scripts/oauth_callback.py first.")
    with open(TOKENS_FILE) as f:
        return json.load(f)

def save_myob_tokens(tokens: dict) -> None:
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKENS_FILE, 0o660)  # group-shared with the mag group - see setup.sh

def refresh_myob_tokens(tokens: dict, client_id: str, client_secret: str) -> dict:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode()
    request = Request(TOKEN_ENDPOINT, method="POST", headers=headers, data=data)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            new_tokens = json.loads(response.read().decode())
    except HTTPError as e:
        raise SystemExit(f"Token refresh failed ({e.code}): {e.read().decode(errors='replace')}")
    except URLError as e:
        raise SystemExit(f"Token refresh failed: could not reach {TOKEN_ENDPOINT} ({e.reason})")

    # The refresh response doesn't repeat businessId/businessName - carry them over.
    new_tokens["businessId"] = tokens.get("businessId")
    new_tokens["businessName"] = tokens.get("businessName")
    save_myob_tokens(new_tokens)
    return new_tokens

def _request_headers(access_token: str, client_id: str) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "x-myobapi-key": client_id,
        "x-myobapi-version": API_VERSION,
        "Accept": "application/json",
    }
    
    cf_username = os.environ.get("MYOB_CF_USERNAME")
    cf_password = os.environ.get("MYOB_CF_PASSWORD")
    if cf_username and cf_password:
        creds = f"{cf_username}:{cf_password}".encode()
        headers["x-myobapi-cftoken"] = base64.b64encode(creds).decode()
    
    return headers

def api_get(path: str, params: dict | None = None) -> dict:
    """GET an AccountRight API path (e.g. "/Sale/Invoice") for the business
    file recorded in tokens.json, refreshing the access token on a 401."""
    client_id = os.environ.get("MYOB_CLIENT_ID")
    client_secret = os.environ.get("MYOB_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set MYOB_CLIENT_ID and MYOB_CLIENT_SECRET environment variables.")
    
    tokens = load_myob_tokens()
    business_id = tokens.get("businessId")
    if not business_id:
        raise SystemExit(f"{TOKENS_FILE} has no businessId — re-run scripts/oauth_callback.py.")
    
    url = f"{API_BASE}/{business_id}{path}"
    if params:
        url += "?" + urlencode(params)
    
    def do_request(access_token: str):
        request = Request(url, headers=_request_headers(access_token, client_id))
        return urlopen(request, timeout=TIMEOUT)
    
    try:
        with do_request(tokens["access_token"]) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        if e.code != 401:
            raise SystemExit(f"API request to {path} failed ({e.code}): {e.read().decode(errors='replace')}")
    except URLError as e:
        raise SystemExit(f"API request to {path} failed: could not reach {url} ({e.reason})")
    
    # Access token expired (401) — refresh once and retry.
    tokens = refresh_myob_tokens(tokens, client_id, client_secret)
    try:
        with do_request(tokens["access_token"]) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        raise SystemExit(f"API request to {path} failed after refresh ({e.code}): {e.read().decode(errors='replace')}")
    except URLError as e:
        raise SystemExit(f"API request to {path} failed: could not reach {url} ({e.reason})")


class UpstreamUnreachable(Exception):
    """MYOB's API couldn't be reached at all (network/DNS/timeout) - as
    opposed to MYOB responding with an HTTP error status, which is a normal
    response to relay, not a failure to raise."""


def raw_request(
    method: str,
    path: str,
    params: dict | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, str | None, bytes]:
    """Forward an arbitrary request to the AccountRight API for the business
    file in tokens.json, refreshing the access token once on a 401.

    Unlike api_get(), MYOB's own error responses are returned rather than
    raised, so a caller (the proxy server) can relay them verbatim - it's
    not this function's job to decide what's a "failure" for the caller.
    Only a genuinely unreachable upstream raises (UpstreamUnreachable).
    """
    client_id = os.environ.get("MYOB_CLIENT_ID")
    client_secret = os.environ.get("MYOB_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set MYOB_CLIENT_ID and MYOB_CLIENT_SECRET environment variables.")
    
    tokens = load_myob_tokens()
    business_id = tokens.get("businessId")
    if not business_id:
        raise SystemExit(f"{TOKENS_FILE} has no businessId — re-run scripts/oauth_callback.py.")
    
    url = f"{API_BASE}/{business_id}{path}"
    if params:
        url += "?" + urlencode(params)
    
    def attempt(access_token: str) -> tuple[int, str | None, bytes]:
        headers = _request_headers(access_token, client_id)
        if body is not None:
            headers["Content-Type"] = content_type or "application/json"
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()
        except URLError as e:
            raise UpstreamUnreachable(str(e.reason))
    
    status, ctype, data = attempt(tokens["access_token"])
    if status == 401:
        tokens = refresh_myob_tokens(tokens, client_id, client_secret)
        status, ctype, data = attempt(tokens["access_token"])
    return status, ctype, data


def api_get_all(path: str, params: dict | None = None, page_size: int = 1000) -> list:
    """GET every page of an AccountRight list endpoint (paging via $top/$skip
    until a page comes back with fewer than page_size items) and return the
    combined Items list. 1000 is MYOB's documented max page size."""
    params = dict(params or {})
    params["$top"] = page_size
    skip = 0
    items = []
    while True:
        params["$skip"] = skip
        page = api_get(path, params=params)
        page_items = page if isinstance(page, list) else page.get("Items", [])
        items.extend(page_items)
        if len(page_items) < page_size:
            return items
        skip += page_size
