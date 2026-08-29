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

## License

Private. Not for distribution.
