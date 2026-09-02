"""Shared helper for the examples/ scripts: call mag's own proxy exactly as
any client (a laptop, a Google Apps Script, ...) would: no MYOB credentials,
no tokens.json, just a mag-issued bearer token and the domain mag is deployed
at. Contrast with examples/no_proxy/, which talks to MYOB directly using the
full OAuth grant.

MAG_DOMAIN / MAG_TOKEN are read from the environment only - unlike
examples/no_proxy/'s MYOB_CLIENT_ID/SECRET, these aren't things a real
client would find in mag's own .env, so there's nothing to load here.

Get a token with `mag issue` (see README's "Issuing tokens") - it needs
the right scope for whatever path is being requested.

To try the commands in list_invoices.py / spend_money_by_supplier.py
yourself, one at a time, from a REPL rather than running the whole script:

    $ cd examples
    $ MAG_DOMAIN=mag.example.com MAG_TOKEN=xxxx python3
    >>> from _proxy_client import proxy_get, proxy_get_all
    >>> proxy_get("/Sale/Invoice", params={"$orderby": "Date desc", "$top": 5})

`cd examples` first so the `from _proxy_client import ...` above can find
this file - it's a sibling, not part of the `mag` package. MAG_DOMAIN and
MAG_TOKEN can also be set after entering the REPL with:
    os.environ["MAG_DOMAIN"] = "mag.example.com"
    os.environ["MAG_TOKEN"] = "mbt_..."
"""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TIMEOUT = 6


def _config() -> tuple[str, str]:
    domain = os.environ.get("MAG_DOMAIN")
    token = os.environ.get("MAG_TOKEN")
    if not domain:
        sys.exit("Set MAG_DOMAIN to the domain mag is deployed at (e.g. mag.example.com).")
    if not token:
        sys.exit("Set MAG_TOKEN to a token from `mag issue` (see README's \"Issuing tokens\").")
    return domain, token


def proxy_get(path: str, params: dict | None = None) -> dict:
    """GET a MYOB API path (e.g. "/Sale/Invoice") through mag's proxy,
    using a mag-issued bearer token. mag relays MYOB's response back
    unmodified, so the result here is identical in shape to a direct call."""
    domain, token = _config()
    url = f"https://{domain}/proxy{path}"
    if params:
        url += "?" + urlencode(params)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
    except HTTPError as e:
        # mag itself returns 401 (no/invalid bearer token) or 403 (token
        # doesn't have this path/method in scope) before ever reaching MYOB;
        # anything else relayed here is MYOB's own error response.
        sys.exit(f"API request to {path} failed ({e.code}): {e.read().decode(errors='replace')}")
    except URLError as e:
        sys.exit(f"API request to {path} failed: could not reach mag at {domain} ({e.reason})")
    return json.loads(data.decode())


def proxy_get_all(path: str, params: dict | None = None, page_size: int = 1000) -> list:
    """GET every page of a MYOB list endpoint through mag's proxy (paging
    via $top/$skip until a page comes back with fewer than page_size items)
    and return the combined Items list. 1000 is MYOB's documented max page
    size."""
    params = dict(params or {})
    params["$top"] = page_size
    skip = 0
    items = []
    while True:
        params["$skip"] = skip
        page = proxy_get(path, params=params)
        page_items = page if isinstance(page, list) else page.get("Items", [])
        items.extend(page_items)
        if len(page_items) < page_size:
            return items
        skip += page_size
