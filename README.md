# mag

An automation tool for [MYOB](https://www.myob.com/). Provides scripting and streamlining of recurring MYOB workflows such as invoice generation.

## Goals

- Automate repetitive MYOB tasks via the MYOB API
- Reduce manual data entry and reporting overhead
- Keep credentials and business data local/private

## Layout

- [`mag/`](mag/) — a plain, uninstalled Python package (no pip/venv - the
  `mag` wrapper and the systemd unit both just put this checkout on
  `PYTHONPATH`; see [Setup](#setup)):
  - [`mag/proxy/`](mag/proxy/) — the persistent application: `proxy.py`, the
    service that keeps running on the server.
  - [`mag/lib/`](mag/lib/) — the library `proxy.py` is built on
    (`myob_client.py`, `token_store.py`), shared with `mag/cli/` and
    `examples/` below - none of it is proxy-specific.
  - [`mag/cli/`](mag/cli/) — one-off tools a human runs by hand, via the
    installed `mag` command (`mag oauth|issue|list|edit|revoke`).
    `__main__.py` is the *only* user interface; `oauth` dispatches to
    `oauth.py`, and `issue`/`list`/`edit`/`revoke` are themselves
    subcommands of `tokens.py` - neither is runnable directly. (Named
    `__main__.py`, not `mag.py` - see its docstring for why.)
- [`examples/`](examples/) — small standalone scripts exercising the MYOB API
  directly via `mag/lib/myob_client.py`, written while exploring what's
  possible (`list_invoices.py`, `spend_money_by_supplier.py`). Unlike
  `mag/`, runnable with no setup beyond MYOB credentials.
- [`tests/`](tests/) — unit and integration tests for `mag/`. See
  [Testing](#testing).
- [`setup.sh`](setup.sh) — installs/updates mag on the server (nginx site,
  `mag-proxy` systemd unit, global `mag` CLI wrapper). [`deploy/`](deploy/)
  holds the templates it uses. See [Setup](#setup).
- [`.env.example`](.env.example) — template for `.env` (gitignored), the
  one place a deployment's MYOB credentials and domain are configured.
  `setup.sh` creates/completes it interactively; see [Setup](#setup).

## Architecture

To avoid introducing a second API, `mag` does not attempt to provide its own endpoints (eg. `POST /api/invoices`). Instead it does two things only:

1. **Issues its own lightweight bearer tokens** to clients. Uses a personal-access-token
   model, like generating a scoped API key in ClickUp or GitHub, rather than
   making every client (eg. GAS, ad hoc scripts, Postman) do MYOB's OAuth2
   dance itself.
2. **Acts as a thin, unopinionated proxy** to the real MYOB API. It forwards
   requests, it doesn't reshape them. Field names, endpoint shapes and error
   bodies pass through unmodified, so MYOB's own docs stay the source of
   truth for callers, and a MYOB schema change never requires a `mag`
   code change — only whichever client reads that particular field.

There is exactly **one** MYOB OAuth grant, held only by `mag`
(`tokens.json`, from `mag/cli/oauth.py`). It is never exposed to a caller.
On the other hand, every client holds one or more `mag`-issued tokens,
scoped far more narrowly than the OAuth grant.

### Negotiation flow

```mermaid
sequenceDiagram
    actor Admin
    participant mag
    participant MYOB as MYOB API
    participant Laptop as Laptop (explore)
    participant GAS as Google Apps Script

    Note over Admin,mag: Issuance - local, out-of-band, one-time per token
    Admin->>mag: mag issue --name ... --scope ...
    mag-->>Admin: token (shown once; only its hash is stored)

    Note over mag,MYOB: One MYOB OAuth grant, shared underneath every client
    Laptop->>mag: GET /proxy/Sale/Invoice<br/>Bearer laptop-explore-token
    mag->>mag: authorize(): scope check
    mag->>MYOB: GET Sale/Invoice<br/>Bearer MYOB access token
    MYOB-->>mag: 200 OK
    mag-->>Laptop: 200 OK

    GAS->>mag: POST /proxy/Sale/Invoice/Item<br/>Bearer gas-invoice-token
    mag->>mag: authorize(): scope check
    mag->>MYOB: POST Sale/Invoice/Item<br/>Bearer MYOB access token
    MYOB-->>mag: 201 Created
    mag-->>GAS: 201 Created

    Note over Laptop,mag: Same token reused later, for an unrelated call
    Laptop->>mag: GET /proxy/Contact/Supplier<br/>Bearer laptop-explore-token
    mag->>mag: authorize(): scope check
    mag->>MYOB: GET Contact/Supplier<br/>Bearer MYOB access token
    MYOB-->>mag: 200 OK
    mag-->>Laptop: 200 OK
```

The two auth levels are never crossed: MYOB only ever sees the one token `mag`
requests; clients only ever see `mag`-issued tokens.

## Setup

Steps to get `mag` running from scratch, in order.

### 1. Get MYOB API credentials

Register an app in the MYOB Developer Centre to get a `MYOB_CLIENT_ID` /
`MYOB_CLIENT_SECRET` pair — see MYOB's
[OAuth2.0 Authentication Guide](https://apisupport.myob.com/hc/en-us/articles/13065472856719)
— with these scopes enabled: `sme-company-file`, `sme-contacts-customer`,
`sme-contacts-supplier`, `sme-sales`, `sme-banking`. These end up in
`.env` (step 3) — nothing is stored in the repo.

### 2. Point a subdomain at this server and get a TLS cert

MYOB's OAuth2 flow requires a registered HTTPS redirect URI. We use
`https://mag.example.com/callback`, served by nginx on the existing
DigitalOcean server — everything below assumes that server, and the nginx
already running on it for other things.

Subdomain added in ICDsoft (though it's not obviously necessary — nginx
will happily terminate TLS for any hostname pointed at the box). Certificate
requested with `certbot`; `certonly` obtains the cert but leaves the nginx
conf untouched, `--nginx` uses nginx itself to serve the verification
challenge:

```bash
sudo certbot certonly --nginx -d mag.example.com
```

### 3. Install mag

```bash
git clone <this-repo-url> /opt/mag   # or wherever - setup.sh doesn't assume a path
cd /opt/mag
sudo ./setup.sh
```

[`setup.sh`](setup.sh) is the entire install *and* update mechanism —
re-run it after a `git pull` to redeploy. It creates a dedicated `mag`
system user, installs the nginx site (`location /callback` and
`location /proxy/`, alongside anything else already on this nginx), the
`mag-proxy` systemd unit (restart-on-crash, start-on-boot — see
[deploy/README.md](deploy/README.md) for why systemd), and a global `mag`
CLI wrapper.

It also prompts, right there in the terminal, for anything missing from
`.env` (a gitignored file at the repo root — see
[`.env.example`](.env.example)): your MYOB credentials from step 1, and
the domain this server is reachable at. `.env` is the one place a fresh
install is configured — no separate secrets directory, no editing
templates by hand — and it's loaded by both the systemd unit and the
`mag` CLI wrapper, so every subsequent `mag` command already has these
values available too.

It also adds you to the `mag` group, so plain `mag`/`git` commands work
without `sudo -u mag` — see [deploy/README.md](deploy/README.md) for why
that's safe here. That only takes effect in a new session, though — start
a fresh login (or run `newgrp mag`) before step 4 below.

### 4. Authorize mag with MYOB

`mag oauth` is a one-shot command that listens for the redirect
registered above, exchanges the authorization code for tokens, and saves
them to `tokens.json`. Run it on the server, once, now that `.env` is
populated:

```bash
mag oauth
```

It prints the MYOB consent URL. Open that in a browser on your own
machine and approve access there. Your browser's redirect hits nginx on
the server, which proxies it to `mag oauth`'s listener, completing the
exchange. Run it once to seed a refresh token — everyday automation reads
from `tokens.json` without needing this step again (until a refresh token
is revoked or expires, in which case: run it again).

Then restart the proxy so it picks up the new `tokens.json`:
`sudo systemctl restart mag-proxy.service`. A client can then call e.g.
`GET https://mag.example.com/proxy/Sale/Invoice` with
`Authorization: Bearer <issued token>` and get MYOB's response back
unmodified — see [Usage](#usage) below for issuing that token. Every
proxied request — success or reject — is appended to `proxy_audit.log`
(`timestamp token-name method path -> status`).

## Usage

Once the proxy is running (see [Setup](#setup)), day-to-day token
management is local and needs no server access — `api_tokens.json` is
created on first use:

```bash
mag issue --name "laptop-explore" \
  --scope "Sale/Invoice:GET" --scope "Contact:GET"
# prints the raw token once - save it, only its hash is stored

mag list                                # audit what exists
mag edit <id> --add-scope "..."         # widen, same secret
mag revoke <id>                         # instant, no MYOB-side effect
```

## Token scope schema

A scope is a `(path prefix, HTTP methods)` pair — deliberately reusing
MYOB's own URL namespace (`Contact/`, `Sale/Invoice`, `Banking/SpendMoneyTxn`,
...) as the vocabulary instead of inventing one, and adding the one
dimension MYOB's own `sme-*` OAuth scopes don't offer: **read vs write, at
the individual endpoint level.**

A token record:

```json
{
  "id": "7f9a2e1c",
  "name": "laptop-explore",
  "token_hash": "sha256:...",
  "scopes": [
    {"prefix": "Sale/Invoice", "methods": ["GET"]},
    {"prefix": "Banking/SpendMoneyTxn", "methods": ["GET"]},
    {"prefix": "Contact", "methods": ["GET"]}
  ],
  "created_at": "2026-08-30T...",
  "last_used_at": "2026-08-31T...",
  "revoked": false
}
```

Checked on every request by the entire authorization surface — small and
disproportionately reviewed/tested, since it's the one thing standing
between the single broad MYOB grant and any given caller:

```python
def authorize(token: str, method: str, path: str) -> bool:
    record = lookup_by_hash(sha256(token))
    if not record or record.revoked:
        return False
    record.last_used_at = now()
    return any(
        path_matches(path, scope["prefix"]) and method in scope["methods"]
        for scope in record.scopes
    )
```

`path_matches` must compare path *segments*, not do a naive
`path.startswith(prefix)` — otherwise a future MYOB endpoint sharing a
string prefix (e.g. a hypothetical `Sale/InvoiceTemplate`) would incorrectly
satisfy a scope meant only for `Sale/Invoice`.

Other properties of the scheme:

- **No master list of endpoints lives inside `mag`.** Prefixes are
  free-form strings matched against whatever path a request actually hits —
  the valid space is just MYOB's own documented namespace, not something
  `mag` mirrors or curates. A typo'd prefix just matches nothing (fails
  closed, safe but confusing) rather than being caught at issuance time.
- **Clients never choose their own scope.** It's set once, by whoever runs
  `mag issue`, at issuance time — not negotiated or selected by the
  client itself. A client only ever discovers its own scope implicitly, via
  which calls succeed.
- **Scopes are mutable independently of the token secret** —
  `mag edit <id> --add-scope "Sale/Invoice:POST"` changes what a token
  can do without rotating the credential itself. Revocation is a separate,
  one-field flip (`revoked: true`), with no effect on MYOB's own grant.
- **`last_used_at` supports pruning stale access** before it's ever an
  incident — a token nobody's used in months is easy to spot and revoke
  proactively rather than discover during a leak investigation.

## Testing

Uses [pytest](https://docs.pytest.org/) + [pytest-mock](https://pytest-mock.readthedocs.io/)
(the only non-stdlib dependencies in this repo - everything it tests
remains stdlib-only; pytest-mock is just `unittest.mock` exposed as a
`mocker` fixture, for consistency with `tmp_path`/`monkeypatch`/`capsys`).
Every test redirects any file a module would touch (`tokens.json`,
`api_tokens.json`, `proxy_audit.log`) to a scratch temp path first, so a
run never reads or writes real MYOB credentials or the real audit log, and
`mag/proxy/proxy.py` / `mag/cli/oauth.py`'s tests spin their real HTTP
servers on an OS-assigned port rather than their hardcoded real one.

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

Run a single file or test with `python3 -m pytest tests/test_token_store.py`
or `-k <name>`.

## License

Private. Not for distribution.
