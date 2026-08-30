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

## License

Private. Not for distribution.
