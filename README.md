# myobot

An automation tool for [MYOB](https://www.myob.com/). Provides scripting and streamlining of recurring MYOB workflows such as invoice generation.

## Status

Early scaffolding — no code yet.

## Goals

- Automate repetitive MYOB tasks via the MYOB API
- Reduce manual data entry and reporting overhead
- Keep credentials and business data local/private

## Setup

_TBD — add install and configuration steps as the project takes shape._

## OAuth redirect server

MYOB's OAuth2 flow requires a registered HTTPS redirect URI. We use:
`https://myobot.example.com/callback`. It is served by nginx on the existing DigitalOcean server.

### nginx config

```nginx
server {
    listen 443 ssl;
    server_name myobot.example.com;

    ssl_certificate     /etc/letsencrypt/live/myobot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/myobot.example.com/privkey.pem;

    location /callback {
        proxy_pass http://127.0.0.1:8787;
    }
}
```

### TLS certificate

Added subdomain: `myobot.example.com` in ICDsoft.

Requested a certificate using the nginx plugin only as the authenticator (`certonly` — obtains the cert but leaves the conf file above untouched):

```bash
sudo certbot certonly --nginx -d myobot.example.com
```

### OAuth callback script

[`scripts/oauth_callback.py`](scripts/oauth_callback.py) is a one-shot script (stdlib only, no dependencies) that listens on `127.0.0.1:8787` for the single redirect described above, exchanges the authorization code for tokens, and saves them to `tokens.json`. Because the listener has to be reachable at the same address nginx proxies to, **run it on the server itself** (e.g. over SSH) — not on your laptop:

```bash
# on the server
MYOB_CLIENT_ID=... MYOB_CLIENT_SECRET=... python3 scripts/oauth_callback.py
```

It prints the MYOB consent URL; open that in a browser on your own machine and approve access there. Your browser's redirect hits nginx on the server, which proxies it to the listener, completing the exchange. Run it once to seed a refresh token — everyday automation reads from `tokens.json` without needing the redirect server again.

> Deployment of the callback listener to the server (as a systemd service, via
> a GitHub Actions workflow) is planned but not yet set up.

## Google Apps Script integration (planned)

Some MYOB automation will be triggered from Google Apps Script (GAS) —
e.g. a script that builds a MYOB invoice from a ClickUp-sourced timesheet
already sitting in a Google Sheet. GAS needs a way to trigger that without
re-implementing MYOB's OAuth2 flow (scopes, refresh, `cftoken`, etc.) a
second time in Apps Script.

**Decision: `myobot` grows a small persistent HTTP API; GAS calls that,
never MYOB directly.** All MYOB-specific knowledge (auth, schema quirks,
field names) stays in one place — this Python codebase — instead of being
duplicated in Apps Script. GAS becomes a thin client: shape sheet data into
a request, POST it, write the result back to the sheet.

| | Google Apps Script | myobot |
|---|---|---|
| ClickUp → timesheet mapping | ✅ | |
| "which sheet rows are this invoice" | ✅ | |
| MYOB auth (OAuth, refresh, scopes) | | ✅ |
| MYOB field/schema knowledge | | ✅ |
| Resolving a customer name → MYOB UID | | ✅ |
| Building the actual MYOB Invoice payload | | ✅ |
| Writing invoice # / status back to the sheet | ✅ | |

`myobot`'s API speaks in domain terms (customer name, period, line items),
not raw MYOB schema — so a future MYOB schema change only touches `myobot`,
never the GAS scripts calling it.

### Negotiation flow

```mermaid
sequenceDiagram
    participant GAS as Google Apps Script
    participant myobot
    participant MYOB as MYOB API

    GAS->>myobot: POST /api/invoices<br/>Authorization: Bearer &lt;myobot API key&gt;
    myobot->>myobot: validate API key + payload
    myobot->>MYOB: create invoice<br/>Authorization: Bearer &lt;MYOB access token&gt;

    alt MYOB access token expired
        MYOB-->>myobot: 401
        myobot->>MYOB: refresh_token grant
        MYOB-->>myobot: new access + refresh token
        myobot->>MYOB: retry: create invoice
    end

    MYOB-->>myobot: invoice created (UID, Number)
    myobot-->>GAS: {invoice_uid, invoice_number, status}
```

Two separate credentials are in play, and neither crosses the boundary it
doesn't belong on: GAS only ever holds the `myobot` API key, never a MYOB
token; `myobot` only ever holds the MYOB OAuth tokens, never exposing them
to callers.

### Managing security by default

- Bearer token between GAS and `myobot`, generated with
  `secrets.token_urlsafe(32)` (as already used for OAuth `state`), compared
  server-side with `hmac.compare_digest` — not `==` — to avoid timing leaks.
- Token provisioned out-of-band, once: generated on the server, pasted into
  GAS's Script Properties by hand — never transmitted automatically, never
  committed to source.
- Per-client keys even with a single caller today, so one integration can be
  revoked/rotated without affecting others, and actions can be attributed
  to a specific key in logs.
- Don't rely on IP allowlisting — Apps Script's `UrlFetchApp` egresses from
  a large, shared Google range; auth is the only real boundary.
- Service binds to `127.0.0.1` only (same pattern as `oauth_callback.py`);
  nginx remains the sole TLS-terminated, internet-facing front door.
- Fail closed on misconfiguration — refuse to start if the API key env var
  is unset, no default/blank-key fallback.
- Minimise and segregate MYOB OAuth scopes — the always-on write service
  shouldn't necessarily carry the same scopes (e.g. `sme-banking`) as the
  manual read-only analysis scripts, so a compromise of one can't reach data
  it was never granted.
- Run as a dedicated non-root user; if under systemd, use its sandboxing —
  `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`,
  `PrivateTmp=true`, `ReadWritePaths=` scoped to just what it needs.
- Strict input validation on every write, independent of auth — customer
  must resolve to a known contact UID, amounts bounded/sane, reject
  unexpected fields.
- Sanity ceiling on auto-created invoices (e.g. require manual confirmation
  above some $ threshold) — a business-logic guardrail, not just infosec.
- Audit log every write: caller key, payload, MYOB's response.
- Sanitise error responses back to callers — log MYOB's raw error bodies
  server-side (may contain account detail), return generic messages out.
- Deploy secrets (MYOB client secret, `myobot` API key) via GitHub Actions
  secrets, injected at deploy time — never committed, never echoed in logs.

## Client access: token issuer + thin proxy (planned)

Refines the transport underneath the [Google Apps Script integration](#google-apps-script-integration-planned)
section above: rather than `myobot` exposing bespoke, domain-shaped
endpoints (`POST /api/invoices`) behind one shared API key, it does two
things only:

1. **Issues its own lightweight bearer tokens** to clients — a personal-access-token
   model, like generating a scoped API key in ClickUp or GitHub, rather than
   making every client (GAS, ad hoc laptop scripts, Postman) do MYOB's OAuth2
   dance itself.
2. **Acts as a thin, unopinionated proxy** to the real MYOB API — it forwards
   requests, it doesn't reshape them. Field names, endpoint shapes and error
   bodies pass through unmodified, so MYOB's own docs stay the source of
   truth for callers, and a MYOB schema change never requires a `myobot`
   code change — only whichever client reads that particular field.

There is exactly **one** MYOB OAuth grant, held only by `myobot`
(`tokens.json`, from `oauth_callback.py`). It is never exposed to a caller.
Every client — however many there are — instead holds one or more
`myobot`-issued tokens, scoped far more narrowly than that one underlying
grant, and reusable across as many calls as that workflow needs.

### Negotiation flow

```mermaid
sequenceDiagram
    actor Admin
    participant myobot
    participant MYOB as MYOB API
    participant Laptop as Laptop (explore)
    participant GAS as Google Apps Script

    Note over Admin,myobot: Issuance - local, out-of-band, one-time per token
    Admin->>myobot: issue_token(name, scopes)
    myobot-->>Admin: token (shown once; only its hash is stored)

    Note over myobot,MYOB: One MYOB OAuth grant, shared underneath every client
    Laptop->>myobot: GET /proxy/Sale/Invoice<br/>Bearer laptop-explore-token
    myobot->>myobot: authorize(): scope check
    myobot->>MYOB: GET Sale/Invoice<br/>Bearer MYOB access token
    MYOB-->>myobot: 200 OK
    myobot-->>Laptop: 200 OK

    GAS->>myobot: POST /proxy/Sale/Invoice/Item<br/>Bearer gas-invoice-token
    myobot->>myobot: authorize(): scope check
    myobot->>MYOB: POST Sale/Invoice/Item<br/>Bearer MYOB access token
    MYOB-->>myobot: 201 Created
    myobot-->>GAS: 201 Created

    Note over Laptop,myobot: Same token reused later, for an unrelated call
    Laptop->>myobot: GET /proxy/Contact/Supplier<br/>Bearer laptop-explore-token
    myobot->>myobot: authorize(): scope check
    myobot->>MYOB: GET Contact/Supplier<br/>Bearer MYOB access token
    MYOB-->>myobot: 200 OK
    myobot-->>Laptop: 200 OK
```

Two auth levels, never crossed: MYOB only ever sees the one grant `myobot`
holds; clients only ever see `myobot`-issued tokens, never a MYOB token.

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

- **No master list of endpoints lives inside `myobot`.** Prefixes are
  free-form strings matched against whatever path a request actually hits —
  the valid space is just MYOB's own documented namespace, not something
  `myobot` mirrors or curates. A typo'd prefix just matches nothing (fails
  closed, safe but confusing) rather than being caught at issuance time.
- **Clients never choose their own scope.** It's set once, by whoever runs
  `issue_token.py`, at issuance time — not negotiated or selected by the
  client itself. A client only ever discovers its own scope implicitly, via
  which calls succeed.
- **Scopes are mutable independently of the token secret** —
  `edit_token.py <id> --add-scope "Sale/Invoice:POST"` changes what a token
  can do without rotating the credential itself. Revocation is a separate,
  one-field flip (`revoked: true`), with no effect on MYOB's own grant.
- **`last_used_at` supports pruning stale access** before it's ever an
  incident — a token nobody's used in months is easy to spot and revoke
  proactively rather than discover during a leak investigation.

## License

Private. Not for distribution.
