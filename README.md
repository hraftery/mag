# mag

The MYOB API Gateway. Provides a convenient Internet endpoint so the [MYOB](https://www.myob.com/) [API](https://developer.myob.com/api/myob-business-api/api-overview/) can be accessed securely from anywhere that can make a web call - a Google Apps Script, your favourite business automation tool, or a script on your own computer.

Must be installed on an Internet accessible server.

## Setup

Steps to get `mag` running from scratch, are as follows.

### 1. Get MYOB API credentials

Register an app in the MYOB Developer Centre (see MYOB's [OAuth2.0 Authentication Guide](https://apisupport.myob.com/hc/en-us/articles/13065472856719)). Enable these scopes: `sme-company-file`, `sme-contacts-customer`, `sme-contacts-supplier`, `sme-sales`, `sme-banking`.

Note the `MYOB_CLIENT_ID` / `MYOB_CLIENT_SECRET` pair for use in step 3. 

### 2. Point a (sub)domain at the server and get a TLS cert

MYOB's OAuth2 flow requires a registered HTTPS redirect URI. Register a domain or add a subdomain and ensure it resolves to your server.

The server requires a web engine to handle the redirect from MYOB. The `setup.sh` script assumes the server is running `nginx` and will produce a suitable site configuration for it. Otherwise manual configuration is required.

The domain requires a TLS certificate. You can generate one with `certbot`:

```bash
sudo certbot certonly --nginx -d <YOUR_DOMAIN>
```

- `certonly` obtains the certificate without adding a site configuration for it. The configuration will be generated in the next step.
- `--nginx` uses nginx itself to serve the certification verification challenge.

### 3. Install mag

```bash
git clone <this-repo-url> /opt/mag   # or wherever - setup.sh doesn't assume a path
cd /opt/mag
sudo ./setup.sh
```

[`setup.sh`](setup.sh) will:

- create a dedicated `mag` user;
- add the nginx site configuration (`location /callback` and `location /proxy/`) using [mag-proxy.conf](templates/mag-proxy.conf) as a template;
- add the `mag-proxy` systemd unit (see
[Why systemd](#why-systemd)) using [mag-proxy.service](templates/mag-proxy.service) as a template;
- and install a global `mag` CLI wrapper using [mag](templates/mag) as a template.

It also prompts for values required to create or update `.env` (based on [`.env.example`](.env.example)). That includes your MYOB credentials from step 1, and the domain this server is reachable at.

`.env` is the key to ensure `mag` can be configured and deployed without a separate secrets store, and the various templates can be instantiated automatically.

`setup.sh` also adds you to the `mag` group, so you can call `mag`/`git` commands. See [Why the `mag` user/group](#why-the-mag-usergroup) for why that's safe here. The new group doesn't take effect immediately, so start
a fresh login or run `newgrp mag` before continuing.

### 4. Authorize `mag` with MYOB

`mag oauth` is a one-shot command that listens for the redirect
registered above, exchanges the authorization code for tokens, and saves them to `tokens.json`. Run it on the server, once, now that `.env` is populated:

```bash
mag oauth
```

It will show the MYOB consent URL. Open that in a browser on your own machine and approve access. Your browser's redirect hits nginx on the server, which proxies it to `mag oauth`'s listener, completing the exchange.

Run it once to seed a refresh token. After that, tokens are read from `tokens.json` without needing this step to be repeated, unless the refresh token is revoked or expires. New tokens are automatically picked up by `mag-proxy.service`.

### 5. Confirm setup

Once `mag oauth` finishes, confirm that setup was successful with:

```bash
mag status
```

Confirm that:

- `mag-proxy.service` is "active (running)".
- MYOB authorisation is "tokens.json present".

`mag` is now ready for clients - see [Usage](#usage) for issuing them a token and how they call the proxy with it.

## Usage

### Issuing tokens

After completing [Setup](#setup), issue a client token with:

```bash
# prints the raw token only once, so be sure to save it
mag issue --name "MyToken" \
  --scope "Sale/Invoice:GET" --scope "Contact:GET"
```

Multiple tokens with different scopes are supported and encouraged. Use a descriptive name to identify them, and see [the list of scopes](https://developer.myob.com/api/myob-business-api/api-overview/granular_data_scopes/) to figure out which ones you need.

Note client tokens are issued without contact with the MYOB API nor the `mag` proxy.

### Using a token

A client can then call the gateway directly with that token:

```bash
curl -H "Authorization: Bearer <issued token>" https://<MAG_DOMAIN>/proxy/Sale/Invoice
```

`mag` forwards the request to MYOB and relays the response back unmodified - see [Architecture](#architecture). Every request is appended to `proxy_audit.log`.

### Editing tokens

Tokens can also be viewed and edited with:

```bash
mag list                           # audit what exists
mag edit <id> --add-scope "..."    # widen, same secret
mag revoke <id>                    # instant, no MYOB-side effect
```

### Checking system status

The `mag` service state, recent logs, MYOB authorisation, issued tokens, and recent proxy activity can all be viewed with:

```bash
mag status
```

This is a good first command when checking on a deployment.

Alternatively, logs can be read directly:

```bash
journalctl -u mag-proxy.service # service lifecycle, stdout
less $MAG_HOME/var/proxy_audit.log # every proxied request
```

### Re-authorising with MYOB

The one MYOB OAuth grant (from [Setup step 4](#4-authorize-mag-with-myob)) can expire or be revoked on MYOB's side independently of anything client tokens do. The MYOB token is unrelated to any individual client token above. If proxied requests start failing for that reason, re-run the same one-shot command:

```bash
mag oauth
```

## Upgrade

Upgrading or downgrading is a two step process. First update `mag` by running `git pull`. Optionally, choose a version other than the lastest with `git checkout` (see "Rollback" below).

Then simply run [`setup.sh`](setup.sh) again to redeploy. It is idempotent and respects configuration that already exists.

Note that `git` is an inherent part of the version management scheme - `setup.sh` will refuse to run against an uncommitted working tree (`git status` must be clean). That's what makes rollback (see below) reliable. Whatever is actually deployed always corresponds to a real commit, never to local edits that can't be retrieved.

### Rollback

Since upgrading is just "point the checkout at a different commit, then redeploy", rolling back is the same operation, backwards:

```bash
git log --oneline       # find the commit to go back to
git checkout <sha>
sudo ./setup.sh
```

## Testing

Uses [pytest](https://docs.pytest.org/) + [pytest-mock](https://pytest-mock.readthedocs.io/)
(the only non-stdlib dependencies in this repo - everything it tests
remains stdlib-only), `pytest-mock` is just `unittest.mock` exposed as a
`mocker` fixture, for consistency with `tmp_path`/`monkeypatch`/`capsys`.

Every test redirects any data file a module needs (`tokens.json`,
`mag_tokens.json`, `proxy_audit.log`) to a scratch temp path first, so a
run never reads or writes real MYOB credentials or the real audit log, and
`mag/proxy/proxy.py` / `mag/cli/oauth.py`'s tests spin their real HTTP
servers on an OS-assigned port rather than their hardcoded real one.

```bash
pip install -r requirements-dev.txt
pytest tests
```

Run a single file or test with `pytest tests/test_token_store.py` or
`-k <name>`.

## Reference

### Layout

- [`mag/`](mag/) — a plain, uninstalled Python package (no pip/venv requirements)
  - [`mag/proxy/`](mag/proxy/) — the persistent application: `proxy.py` is a service that runs on the server and provides the gateway to the MYOB API.
  - [`mag/lib/`](mag/lib/) — shared functionality.
  - [`mag/cli/`](mag/cli/) — a command-line interface to perform individual tasks.
- [`examples/`](examples/) — standalone scripts that demonstrate how to exercise the MYOB API via `mag/lib/myob_client.py`.
- [`tests/`](tests/) — unit and integration tests. See
  [Testing](#testing).
- [`setup.sh`](setup.sh) — installs or updates `mag` on the server. See [Setup](#setup).
- [`templates/`](templates/)
  holds the templates used by `setup.sh`.
- [`.env.example`](.env.example) — template for `.env`, the local store for MYOB credentials and domain created by `setup.sh`.

### Architecture

`mag` sets up a gateway that redirects requests from authenticated clients to the MYOB API. To avoid introducing a second API, `mag` does not attempt to provide its own endpoints (eg. `POST /api/invoices`). Instead it does two things only:

1. **Issues its own lightweight bearer tokens** to clients. Uses a personal-access-token model, like generating a scoped API key in ClickUp or GitHub. This relieves every client (eg. GAS, ad hoc scripts, Postman) from having to do MYOB's OAuth2 dance itself.
2. **Acts as a thin, unopinionated proxy** to the real MYOB API. It forwards requests without modification, so MYOB's own docs stay the source of truth for callers and changes to MYOB's schema don't require a change to `mag`.

There is exactly **one** MYOB OAuth grant, held only by `mag` (`tokens.json`, from `mag/cli/oauth.py`). It is never exposed to a caller. On the other hand, every client holds one or more `mag`-issued tokens,
scoped far more narrowly than the OAuth grant.

#### Negotiation flow

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

The two auth levels are never crossed: MYOB only ever sees the token `mag` requests and clients only ever see `mag`-issued tokens.

### Why systemd

The proxy server ([`proxy.py`](mag/proxy/proxy.py)) benefits from restart-on-crash, start-on-boot, and centralised logs. `systemd` is widely pre-installed and is built for supervising a network daemon like this.

### Why the `mag` user/group

The runtime data files, (`tokens.json`, `mag_tokens.json`, `.env`) need to be readable and
writable both by the `mag-proxy` systemd unit (which runs as the `mag` user) and by the login user that runs `mag` commands by hand. Instead of requiring `sudo -u mag` before every command to keep file ownership from drifting, the login user is added to the `mag` group. The setuid bit is ignored on scripts, so instead `setup.sh` group-shares the checkout: files are `660` rather than `600`, and whoever ran `setup.sh` (via `sudo`) is added to the `mag` group. This doesn't reduce security because that's already someone with root on this box. Group access grants nothing `sudo` doesn't already give them.

### Token scope schema

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

## License

Private. Not for distribution.
